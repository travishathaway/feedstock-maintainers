# `geonames_cities15000.json.gz`

A trimmed snapshot of [GeoNames](https://www.geonames.org/)' `cities15000` dump (all cities with
population >= 15,000), used by `feedstock_maintainers.countries` to resolve a free-text location
like `"Bangalore"` to a country. Contains only what that module needs -- name + ASCII alternate
names, country code, and population -- as a JSON array of `[names, country_code, population]`
rows, gzip-compressed.

**License:** GeoNames data is licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/);
this file is a derivative (reduced to three fields, English/ASCII names only). Attribution:
"Contains information from [GeoNames](https://www.geonames.org/), which is made available here
under the [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/)."

**Regenerating** (occasional, e.g. to pick up new/renamed cities -- not part of any pipeline):

```bash
curl -LO https://download.geonames.org/export/dump/cities15000.zip
unzip cities15000.zip
python3 - <<'EOF'
import csv, gzip, json

rows = []
with open("cities15000.txt", encoding="utf-8") as f:
    for r in csv.reader(f, delimiter="\t"):
        name, asciiname, alternatenames, country_code = r[1], r[2], r[3], r[8]
        population = int(r[14]) if r[14] else 0
        names = {name, asciiname}
        if alternatenames:
            names.update(a for a in alternatenames.split(",") if a.isascii())
        names.discard("")
        rows.append([sorted(names), country_code, population])
rows.sort(key=lambda row: (row[1], -row[2], row[0]))

with gzip.open("geonames_cities15000.json.gz", "wb", compresslevel=9) as f:
    f.write(json.dumps(rows, separators=(",", ":")).encode("utf-8"))
EOF
```
