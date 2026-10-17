import xml.etree.ElementTree as ET
import csv

KML_FILE = "/home/centrabus/centrabus_route.kml"
CSV_FILE = "/home/centrabus/reference_route.csv"

tree = ET.parse(KML_FILE)
root = tree.getroot()

route_points = []

for element in root.iter():
    if element.tag.endswith("LineString"):
        for child in element.iter():
            if child.tag.endswith("coordinates") and child.text:
                for item in child.text.strip().split():
                    parts = item.split(",")

                    if len(parts) >= 2:
                        longitude = float(parts[0])
                        latitude = float(parts[1])

                        route_points.append(
                            (latitude, longitude)
                        )

if len(route_points) < 2:
    raise ValueError(
        "No valid route LineString was found in the KML file."
    )

with open(CSV_FILE, "w", newline="") as file:
    writer = csv.writer(file)

    writer.writerow([
        "latitude",
        "longitude"
    ])

    writer.writerows(route_points)

print()
print("Reference route created successfully.")
print("Total route points:", len(route_points))
print("Saved to:")
print(CSV_FILE)