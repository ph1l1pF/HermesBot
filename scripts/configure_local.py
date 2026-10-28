"""Configure credentials through hidden terminal input; never print secrets."""
import argparse
import getpass
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--environment', choices=['test', 'production'], default='production')
args = parser.parse_args()
variable = 'CARGOBOARD_SANDBOX_API_KEY' if args.environment == 'test' else 'CARGOBOARD_PRODUCTION_API_KEY'
root = Path(__file__).resolve().parents[1]
path = root / '.env'
values = path.read_text() if path.exists() else (root / '.env.example').read_text()
key = getpass.getpass(f'Cargoboard API-Key für {args.environment} (Eingabe unsichtbar): ').strip()
if not key or any(char in key for char in '\r\n\x00'):
    raise SystemExit('Kein gültiger Key eingegeben. Datei wurde nicht geändert.')
# Quote and escape using dotenv syntax; never execute entered values as shell code.
encoded = key.replace('\\', '\\\\').replace('"', '\\"')
lines = [line for line in values.splitlines() if not line.startswith(variable + '=')]
lines.append(f'{variable}="{encoded}"')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('\n'.join(lines) + '\n')
print('Key in lokaler .env gespeichert. Start: ./scripts/start_local.sh')
