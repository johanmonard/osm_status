import requests
query='''[out:json][timeout:120];
(
way["highway"](19.66,99.09,19.81,99.21);
relation["highway"](19.66,99.09,19.81,99.21);
);
out geom tags;
'''
r=requests.post('https://overpass-api.de/api/interpreter', data={'data': query})
print(r.status_code)
print(r.text[:200])
