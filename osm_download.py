import os
import re
import io
from contextlib import redirect_stdout, redirect_stderr
from collections import defaultdict

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import LineString, MultiPolygon, Polygon, shape
from osm2geojson import json2geojson

# -----------------------------------------------------------------------------
# DEFAULT LAYERS
# -----------------------------------------------------------------------------

DEFAULT_LAYERS = {
    "roads": {
        "filters": ['["highway"]'],
        "geometry": "line"
    },
    "powerlines": {
        "filters": ['["power"="line"]', '["power"="minor_line"]'],
        "geometry": "line"
    },
    "landuse": {
        "filters": ['["landuse"]'],
        "geometry": "polygon"
    },
    "forests": {
        "filters": ['["natural"="wood"]', '["landcover"="trees"]'],
        "geometry": "polygon"
    },
    "scrub": {
        "filters": ['["natural"="scrub"]', '["natural"="heath"]'],
        "geometry": "polygon"
    },
    "bare_ground": {
        "filters": ['["natural"="bare_rock"]', '["natural"="sand"]'],
        "geometry": "polygon"
    },
    "transport": {
        "filters": ['["aeroway"]', '["amenity"="parking"]'],
        "geometry": "polygon"
    },
    "buildings": {
        "filters": ['["building"]'],
        "geometry": "polygon"
    },
    "parks": {
        "filters": ['["leisure"="park"]', '["leisure"="garden"]', '["leisure"="nature_reserve"]'],
        "geometry": "polygon"
    },
    "water": {
        "filters": [
            '["natural"="water"]',
            '["waterway"]',
            '["landuse"="reservoir"]',
            '["natural"="bay"]'
        ],
        "geometry": "polygon"
    },
    "shore_structures": {
        "filters": ['["man_made"="pier"]', '["man_made"="harbour"]'],
        "geometry": "polygon"
    },
    "dams": {
        "filters": ['["waterway"="dam"]', '["waterway"="weir"]'],
        "geometry": "line"
    },
    "railways": {
        "filters": ['["railway"]'],
        "geometry": "line"
    },
}

# -----------------------------------------------------------------------------
# OVERPASS: FULL GEOMETRY DOWNLOAD (out:json + geometry)
# -----------------------------------------------------------------------------

def overpass_download_geoms(filters, polygon):
    """
    Télécharge les géométries OSM via Overpass et extrait correctement les fclass.
    """

    bbox = polygon.bounds
    west, south, east, north = bbox

    filter_specs = []
    pattern = re.compile(r'\["([^"]+)"(?:="([^"]+)")?\]')
    for fl in filters:
        match = pattern.search(fl)
        if match:
            filter_specs.append((match.group(1), match.group(2)))
        else:
            filter_specs.append((fl, None))

    def _build_block(fltk):
        return [
            f"way{fltk}({south},{west},{north},{east});",
            f"relation{fltk}({south},{west},{north},{east});",
        ]

    body_lines = []
    for fl in filters:
        body_lines.extend(_build_block(fl))
    body = "\n".join(body_lines)
    query = (
        "[out:json][timeout:120];\n"
        "(\n"
        f"{body}\n"
        ");\n"
        "(._;>;);\n"
        "out body geom;"
    )
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": query})
    if r.status_code != 200:
        raise RuntimeError("Overpass Error: " + r.text)

    data = r.json()

    try:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            geojson = json2geojson(data)
    except Exception as exc:
        raise RuntimeError("Overpass conversion error") from exc

    geoms = []
    fclasses = []
    for feature in geojson.get("features", []):
        geom = feature.get("geometry")
        if not geom:
            continue
        try:
            shp = shape(geom)
        except Exception:
            continue

        props = feature.get("properties", {}) or {}
        tags = props.get("tags", {}) if isinstance(props.get("tags"), dict) else {}
        fclass = None
        for key, value in filter_specs:
            tag_val = props.get(key)
            if tag_val is None:
                tag_val = tags.get(key)
            if tag_val is None:
                continue
            if value is not None and tag_val != value:
                continue
            fclass = tag_val
            break

        if fclass is None:
            continue

        geoms.append(shp)
        fclasses.append(fclass)

    if not geoms:
        return gpd.GeoDataFrame(columns=["geometry", "fclass"], crs="EPSG:4326")

    df = pd.DataFrame({"geometry": geoms, "fclass": fclasses})
    return gpd.GeoDataFrame(df, crs="EPSG:4326")


# -----------------------------------------------------------------------------
# STATISTICS
# -----------------------------------------------------------------------------

def compute_statistics(gdf, polygon):
    if gdf.empty:
        return {"total_km2": 0, "total_km": 0}

    clip = gpd.overlay(
        gdf,
        gpd.GeoDataFrame(geometry=[polygon], crs="EPSG:4326"),
        how="intersection",
    )

    if clip.empty:
        return {"total_km2": 0, "total_km": 0}

    clip_m = clip.to_crs(3857)
    poly_area_km2 = clip_m.area.sum() / 1_000_000
    line_km = clip_m.length.sum() / 1000

    return {"total_km2": poly_area_km2, "total_km": line_km, "gdf": clip}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _flatten_geometrycollections(gdf):
    """
    Split GeometryCollections produced by overlay into their constituent parts
    so polygon/line pieces can be measured.
    """

    def _iter_parts(geom):
        if geom is None or geom.is_empty:
            return
        if geom.geom_type == "GeometryCollection":
            for part in geom:
                yield from _iter_parts(part)
        else:
            yield geom

    rows = []
    for _, row in gdf.iterrows():
        base = row.drop(labels="geometry").to_dict()
        for part in _iter_parts(row.geometry):
            rec = dict(base)
            rec["geometry"] = part
            rows.append(rec)

    if not rows:
        return gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)

    return gpd.GeoDataFrame(rows, columns=gdf.columns, crs=gdf.crs)

# MAIN FUNCTION
# -----------------------------------------------------------------------------

def download_crop_osm(layers=None, polygon=None, target_folder="output"):
    if layers is None:
        layers = list(DEFAULT_LAYERS.keys())

    os.makedirs(target_folder, exist_ok=True)

    results = {}
    geoms = {}

    for layer in layers:
        geom_type = DEFAULT_LAYERS[layer]["geometry"]
        print(f"--- Layer {layer} ---")

        cfg = DEFAULT_LAYERS[layer]
        filters = cfg["filters"]

        gdf = overpass_download_geoms(filters, polygon)

        # Filtrage strict selon le type attendu
        if geom_type == "line":
            gdf = gdf[gdf.geometry.type == "LineString"]
        elif geom_type == "polygon":
            gdf = gdf[gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
        stats = compute_statistics(gdf, polygon)
        results[layer] = stats

        if "gdf" in stats and not stats["gdf"].empty:
            clean_gdf = _flatten_geometrycollections(stats["gdf"])
            if not clean_gdf.empty:
                stats["gdf"] = clean_gdf
                clean_gdf.to_file(os.path.join(target_folder, f"{layer}.gpkg"), driver="GPKG")
                geoms[layer] = clean_gdf

        # Impression des statistiques
    for layer, stats in results.items():
        print(f"Layer {layer} : {stats['total_km2']:.3f} km², {stats['total_km']:.3f} km")

    return results, geoms

# -----------------------------------------------------------------------------
# KML LOADER
# -----------------------------------------------------------------------------

def load_kml_polygon(path):
    gdf = gpd.read_file(path, driver="KML")
    if gdf.empty:
        raise ValueError("KML vide.")

    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    else:
        gdf = gdf.to_crs(4326)

    poly = gdf.geometry.union_all()

    if not isinstance(poly, (Polygon, MultiPolygon)):
        raise ValueError("Le KML n'est pas un polygone.")

    if poly.is_empty:
        raise ValueError("Le KML contient une géométrie vide.")

    return poly

# -----------------------------------------------------------------------------
# PLOT FUNCTIONS BY FCLASS
# -----------------------------------------------------------------------------

def plot_top_fclass(results, geoms, target_folder="output", n=5, mode="area", reference=None, polygon=None):
    """
    Backwards-compatible helper that saves plots instead of opening them.
    """
    fclass_stats = compute_fclass_statistics(results)
    save_fclass_plot(
        fclass_stats,
        geoms,
        target_folder,
        n=n,
        mode=mode,
        reference=reference,
        polygon=polygon,
    )

# -----------------------------------------------------------------------------
# FCLASS STATISTICS
# -----------------------------------------------------------------------------

def compute_fclass_statistics(results):
    area_totals = defaultdict(float)
    length_totals = defaultdict(float)

    for _, data in results.items():
        gdf = data.get("gdf")
        if gdf is None or gdf.empty or "fclass" not in gdf.columns:
            continue

        gdf_m = gdf.to_crs(3857)
        gdf_m = _flatten_geometrycollections(gdf_m)
        if gdf_m.empty:
            continue
        polygons = gdf_m[gdf_m.geom_type.isin(["Polygon", "MultiPolygon"])]
        if not polygons.empty:
            for fclass, sub in polygons.groupby("fclass"):
                area_totals[fclass] += sub.area.sum() / 1_000_000

        lines = gdf_m[gdf_m.geom_type.isin(["LineString", "MultiLineString"])]
        if not lines.empty:
            for fclass, sub in lines.groupby("fclass"):
                length_totals[fclass] += sub.length.sum() / 1000

    return {"area": dict(area_totals), "length": dict(length_totals)}

# -----------------------------------------------------------------------------
# PRINT FCLASS STATS
# -----------------------------------------------------------------------------

def print_fclass_stats(fclass_stats, n=5, reference=None):
    ref = reference or {}

    def _print_section(title, values, unit, ref_key):
        if not values:
            return
        print(f"\n{title}:")
        items = sorted(values.items(), key=lambda x: x[1], reverse=True)
        if n is not None:
            items = items[:n]
        ref_value = ref.get(ref_key)
        for fclass, total in items:
            pct = (total / ref_value * 100) if ref_value and ref_key == "area" else None
            pct_str = f" ({pct:.1f}%)" if pct is not None else ""
            print(f"  {fclass}: {total:.3f} {unit}{pct_str}")

    _print_section("Surface (km²)", fclass_stats.get("area", {}), "km²", "area")
    _print_section("Length (km)", fclass_stats.get("length", {}), "km", "length")

# -----------------------------------------------------------------------------
# SAVE PLOTS
# -----------------------------------------------------------------------------

def save_fclass_plot(
    fclass_stats,
    geoms,
    target_folder,
    n=5,
    mode="area",
    reference=None,
    polygon=None,
):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.cm as cm

    metric_key = "area" if mode == "area" else "length"
    metric_values = fclass_stats.get(metric_key, {})
    if not metric_values:
        return

    ref_value = reference.get(metric_key) if reference else None
    if ref_value and mode == "area":
        sort_key = lambda x: x[1] / ref_value
    else:
        sort_key = lambda x: x[1]
    items = sorted(metric_values.items(), key=sort_key, reverse=True)
    if n is not None:
        items = items[:n]

    total_percent = None
    if ref_value and mode == "area":
        total_percent = sum((total / ref_value * 100) for _, total in items if ref_value)

    colors = cm.tab20.colors

    fig, ax = plt.subplots(figsize=(14, 14))

    geom_filter = {
        "area": ["Polygon", "MultiPolygon"],
        "length": ["LineString", "MultiLineString"],
    }[mode]

    plot_items = []
    for fclass, total in items:
        subsets = []
        for layer_gdf in geoms.values():
            if "fclass" not in layer_gdf.columns:
                continue
            sub = layer_gdf[layer_gdf["fclass"] == fclass]
            if sub.empty:
                continue
            sub = sub[sub.geom_type.isin(geom_filter)]
            if sub.empty:
                continue
            subsets.append(sub)

        if not subsets:
            continue

        plot_items.append((fclass, total, subsets))

    if not plot_items:
        return

    color_map = {f: colors[i % len(colors)] for i, (f, _, _) in enumerate(plot_items)}
    legend_handles = []
    unit_label = "km²" if mode == "area" else "km"

    for fclass, total, subsets in plot_items:
        merged = gpd.GeoDataFrame(pd.concat(subsets), crs=subsets[0].crs)
        merged.to_crs(4326).plot(
            ax=ax,
            color=color_map[fclass],
            alpha=0.5,
            linewidth=0.8,
        )

        pct_str = ""
        if ref_value and mode == "area":
            pct = total / ref_value * 100
            pct_str = f", {pct:.1f}%"

        legend_label = f"{fclass} ({total:.1f} {unit_label}{pct_str})"
        legend_handles.append(mpatches.Patch(color=color_map[fclass], label=legend_label))

    if polygon is not None:
        boundary = gpd.GeoSeries([polygon], crs="EPSG:4326")
        boundary.plot(
            ax=ax,
            facecolor="none",
            edgecolor="black",
            linewidth=1.2,
            zorder=20,
        )

    title = f"Top fclass ({unit_label})"
    if total_percent is not None and mode == "area":
        title = f"{title}, {total_percent:.1f}% of polygon"
    ax.legend(handles=legend_handles, title=title)

    plt.tight_layout(pad=0.3)
    out = os.path.join(target_folder, f"top_fclass_{mode}.png")
    plt.savefig(out, dpi=300)
    plt.close()

# -----------------------------------------------------------------------------
# TEST
# -----------------------------------------------------------------------------

if __name__ == "__main__":

    poly = load_kml_polygon(r"lake2lake_poly.kml")
    # poly = load_kml_polygon(r"annecy.kml")
    # poly = load_kml_polygon(r"South Fang.kml")
    # poly = load_kml_polygon(r"cuba3d.kml")
    # poly = load_kml_polygon(r"ramhan.kml")
    # poly = load_kml_polygon(r"North East Obaiyed & North Matruh.kml")

    
    

    target_folder = r"output"
    poly_copy = shape(poly.__geo_interface__)
    poly_m = gpd.GeoSeries([poly_copy], crs="EPSG:4326").to_crs(3857).iloc[0]
    polygon_reference = {
        "area": poly_m.area / 1_000_000,
        "length": poly_m.length / 1000,
    }

    results, geoms = download_crop_osm(
        layers=None,
        polygon=poly,
        target_folder=target_folder,
    )

    # --- Affichage des statistiques ---
    print("=== STATISTIQUES ===")
    for layer, stats in results.items():
        print(f"{layer}: {stats['total_km2']:.3f} km², {stats['total_km']:.3f} km")

    fclass_stats = compute_fclass_statistics(results)
    print("\n=== STATISTIQUES PAR FCLASS ===")
    print_fclass_stats(fclass_stats, n=10, reference=polygon_reference)

    save_fclass_plot(
        fclass_stats,
        geoms,
        target_folder,
        n=25,
        mode="area",
        reference=polygon_reference,
        polygon=poly,
    )
    save_fclass_plot(
        fclass_stats,
        geoms,
        target_folder,
        n=25,
        mode="length",
        reference=polygon_reference,
        polygon=poly,
    )


