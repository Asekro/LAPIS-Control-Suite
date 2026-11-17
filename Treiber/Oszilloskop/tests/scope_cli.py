"""Optionaler Hardwaretest im Terminal, ohne Dateiexport.

Standard: nur Gerätekennung und Status lesen. Aufrufe aus dem Treiberordner:
python tests/scope_cli.py --list
python tests/scope_cli.py --resource "USB0::...::INSTR"
python tests/scope_cli.py --resource "USB0::...::INSTR" --read --channels 1 --plot
python tests/scope_cli.py --resource "USB0::...::INSTR" --capture --channels 1
"""
import argparse
import json
from pathlib import Path
import sys

# Direkter Skriptaufruf funktioniert auch aus einem anderen Arbeitsverzeichnis.
driver_dir = str(Path(__file__).resolve().parents[1])
if driver_dir not in sys.path:
    sys.path.insert(0, driver_dir)

from keysight_dsox1204g import KeysightDSOX1204G


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--resource")
    parser.add_argument("--backend", default="")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--read", action="store_true", help="Gerät stoppen, bestehende Aufnahme lesen")
    action.add_argument("--capture", action="store_true", help="Neue Aufnahme mit der eingestellten Triggerquelle")
    parser.add_argument("--channels", type=int, nargs="+", choices=range(1, 5), default=[1, 2])
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    if args.list:
        for resource in KeysightDSOX1204G.list_resources(args.backend):
            print(resource)
        return
    if not args.resource:
        parser.error("--resource angeben; verfügbare Adressen mit --list anzeigen.")
    with KeysightDSOX1204G(args.resource, visa_backend=args.backend) as scope:
        print(scope.idn)
        state = scope.status()
        print(json.dumps(state, indent=2, ensure_ascii=False))
        if not (args.read or args.capture):
            return
        if args.capture:
            # Vorhandene Pegel/Skalen und Triggerquelle bleiben erhalten.
            # Kanäle bewusst aktivieren, MAIN und eine einzelne Aufnahme wählen.
            scope.write_scpi(":TIMebase:MODE MAIN")
            scope.configure_acquisition("NORMAL")
            for ch in args.channels:
                scope.configure_channel(ch, enabled=True)
            scope.arm_single()
            scope.wait_until_armed()
            print("Bereit: warte auf die eingestellte Triggerquelle. WaveGen wird hier nicht eingeschaltet.", flush=True)
            scope.wait_for_single(timeout_s=args.timeout)
        waves = scope.read_waveforms(args.channels)
        for ch, wave in waves.items():
            print(f"CH{ch}: {wave.raw.size} Punkte; dt={wave.preamble['x_increment']:.6g} s; "
                  f"fehlend={wave.missing.sum()}, abgeschnitten={(wave.clipped_low | wave.clipped_high).sum()}")
    if args.plot and (args.read or args.capture):
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(len(waves), 1, sharex=True, squeeze=False)
        for ax, (ch, wave) in zip(axes.flat, waves.items()):
            ax.plot(wave.time_s * 1e6, wave.voltage_v, linewidth=0.8)
            ax.set_ylabel(f"CH{ch} / V")
            ax.grid(alpha=0.2)
        axes[-1, 0].set_xlabel("Zeit relativ zur Gerätezeitreferenz / µs")
        fig.tight_layout()
        plt.show()
    return waves


if __name__ == "__main__":
    main()
