"""Nur lesen: python test_verbindung.py --positive COM7 --negative COM8
Ausgänge und Sollwerte werden nicht verändert. Mit Strg+C abbrechen.
"""
import argparse
import logging
from iseg_nhs import Bender

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--positive', required=True)
    parser.add_argument('--negative', required=True)
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    with Bender(args.positive, args.negative, max_voltage=100) as bender:
        for name, module in [('Positiv', bender.positive), ('Negativ', bender.negative)]:
            print(name, module.read_module())
        for name, reading in bender.read_all().items():
            print(f'{name}: Soll={reading.voltage_set:g} V, Ist={reading.voltage:g} V, '
                  f'I={reading.current:.6g} A, Status=0x{int(reading.status):04X} '
                  f'({reading.status!r}), Events=0x{int(reading.events):04X} ({reading.events!r})')

if __name__ == '__main__': main()
