"""Separater, expliziter WaveGen-Test: zuerst Last und Ausgang am Oszi prüfen.

Default 'configure' setzt Werte, Ausgang bleibt aus. 'continuous' gibt Pulse
für --seconds aus, 'gate' probiert den nicht garantierten Ein/Aus-Trick.
"""
import argparse
from pathlib import Path
import sys
import time

driver_dir = str(Path(__file__).resolve().parents[1])
if driver_dir not in sys.path:
    sys.path.insert(0, driver_dir)

from keysight_dsox1204g import KeysightDSOX1204G
from lapis_workflows import experimental_wavegen_gate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--load", choices=("HIGHZ", "50OHM"), required=True)
    parser.add_argument("--mode", choices=("configure", "continuous", "gate"), default="configure")
    parser.add_argument("--frequency", type=float, default=1.0)
    parser.add_argument("--width-us", type=float, default=20)
    parser.add_argument("--seconds", type=float, default=3)
    args = parser.parse_args()
    if args.seconds <= 0 or args.seconds > 60:
        parser.error("--seconds muss zwischen 0 und 60 liegen.")
    with KeysightDSOX1204G(args.resource) as scope:
        print(scope.configure_ttl(load=args.load, frequency_hz=args.frequency,
                                  pulse_width_s=args.width_us * 1e-6))
        if args.mode == "continuous":
            try:
                scope.wavegen_output(True)
                time.sleep(args.seconds)
            finally:
                scope.wavegen_output(False)
        elif args.mode == "gate":
            experimental_wavegen_gate(scope, on_time_s=0.2)
            print("Ein/Aus-Versuch beendet. Die Anzahl der Pulse wurde nicht gemessen.")


if __name__ == "__main__":
    main()
