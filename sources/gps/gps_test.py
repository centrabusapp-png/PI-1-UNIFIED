from gps import gps, WATCH_ENABLE, WATCH_NEWSTYLE

session = gps(mode=WATCH_ENABLE | WATCH_NEWSTYLE)

satellites_seen = None
satellites_used = None
hdop = None

print("Waiting for GPS data...")
print("Press Ctrl+C to stop.\n")

try:
    for report in session:

        # Satellite information
        if report["class"] == "SKY":
            satellites_seen = report.get("nSat")
            satellites_used = report.get("uSat")
            hdop = report.get("hdop")

        # Position information
        elif report["class"] == "TPV":
            mode = report.get("mode", 0)

            if mode < 2:
                print("Waiting for GPS fix...")
                continue

            latitude = report.get("lat")
            longitude = report.get("lon")
            altitude = report.get("altMSL")
            speed_mps = report.get("speed")
            gps_time = report.get("time")

            if speed_mps is not None:
                speed_kmh = speed_mps * 3.6
            else:
                speed_kmh = None

            print("========== NEO-M8N GPS ==========")
            print(f"Latitude        : {latitude}")
            print(f"Longitude       : {longitude}")
            print(f"Altitude MSL    : {altitude} m")
            print(f"Speed           : {speed_kmh} km/h")
            print(f"Satellites Seen : {satellites_seen}")
            print(f"Satellites Used : {satellites_used}")
            print(f"HDOP            : {hdop}")
            print(f"Fix Mode        : {mode}")
            print(f"GPS Time        : {gps_time}")
            print("=================================\n")

except KeyboardInterrupt:
    print("\nGPS monitoring stopped.")


