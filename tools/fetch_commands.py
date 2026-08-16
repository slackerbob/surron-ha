"""One-time fetch of the real controller command set from Sur-Ron's cloud.

The official app never uses fixed commands for telemetry — it downloads a per-bike command
list from Sur-Ron's server and sends those. This tool logs into your Sur-Ron account (the
same email/password you use in the app) and fetches that command list, so we can bake the
real commands into the local integration. It also grabs the state "type" list and your bike
list for context.

This talks to Sur-Ron's servers with YOUR credentials — it's your account and your data. The
password is sent exactly the way the app sends it (MD5, upper-cased) over HTTPS; nothing is
stored except the JSON you ask it to save. Standard library only (no pip installs).

Endpoints/headers/auth were reconstructed from the app:
- base https://osgateway.sur-ron.com/
- login: POST md-app-common/loginByPassword  {email, password: MD5(pw).upper()} -> data.token
- authed GETs carry a "token" header; responses are {code:200, data:...}

Usage
-----
    python fetch_commands.py --email you@example.com
    python fetch_commands.py --email you@example.com --password 'secret' --out surron_cloud.json
    python fetch_commands.py --token <existing-token>        # skip login if you already have one

Then feed the saved file to the capture tool:
    python capture.py --name "QL-..." --commands surron_cloud.json --soc 100
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
import urllib.error
import urllib.request

BASE = "https://osgateway.sur-ron.com/"
PNAME = "md-app/"
UNAME = "md-app-common/"

# Header values mirror the app (v2.0.1). The server mainly cares about token + content-type;
# the rest are sent for parity and can be overridden if ever needed.
BASE_HEADERS = {
	"content-type": "application/json",
	"language": "cn",
	"platform": "android",
	"appVersion": "2.0.1",
	"osVersion": "android",
	"deviceType": "phone",
	"clientId": "",
}


def _md5_upper(text: str) -> str:
	return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


def _request(url: str, method: str, token: str | None, body: dict | None, timeout: float) -> dict:
	"""Make one JSON request and return the parsed envelope {code, data, msg}."""
	headers = dict(BASE_HEADERS)
	if token:
		headers["token"] = token
	data_bytes = None
	if body is not None:
		data_bytes = json.dumps(body).encode("utf-8")
	req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			raw = resp.read().decode("utf-8")
	except urllib.error.HTTPError as exc:
		raw = exc.read().decode("utf-8", "replace")
		print(f"HTTP {exc.code} for {url}:\n{raw}", file=sys.stderr)
		raise
	try:
		return json.loads(raw)
	except json.JSONDecodeError:
		print(f"Non-JSON response from {url}:\n{raw[:500]}", file=sys.stderr)
		raise


def login(email: str, password: str, timeout: float) -> str:
	"""Return an auth token for the account, or raise."""
	url = BASE + UNAME + "loginByPassword"
	body = {"email": email, "password": _md5_upper(password)}
	env = _request(url, "POST", None, body, timeout)
	if env.get("code") != 200:
		raise SystemExit(f"Login failed: code={env.get('code')} msg={env.get('msg')!r}")
	token = (env.get("data") or {}).get("token")
	if not token:
		raise SystemExit(f"Login returned no token: {env}")
	data = env["data"]
	print(
		f"Logged in. userId={data.get('userId')} bikes bound={data.get('bindBikeCount')}",
		file=sys.stderr,
	)
	return token


def _get(path_prefix: str, endpoint: str, token: str, timeout: float, params: dict | None = None) -> dict:
	url = BASE + path_prefix + endpoint
	if params:
		import urllib.parse

		url += "?" + urllib.parse.urlencode(params)
	return _request(url, "GET", token, None, timeout)


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Fetch Sur-Ron BT command list (one-time)")
	parser.add_argument("--email", help="Account email")
	parser.add_argument("--password", help="Account password (prompted if omitted)")
	parser.add_argument("--token", help="Existing auth token (skips login)")
	parser.add_argument("--out", default="surron_cloud.json", help="Where to save the results")
	parser.add_argument("--timeout", type=float, default=20.0)
	args = parser.parse_args(argv)

	token = args.token
	if not token:
		if not args.email:
			parser.error("give --email (and --password), or --token")
		password = args.password or getpass.getpass("Sur-Ron password: ")
		token = login(args.email, password, args.timeout)

	results: dict = {"base": BASE, "token_present": bool(token)}

	# The command list is the prize; the rest is context.
	fetches = {
		"loadAllStateBtCommand": (PNAME, "motor-state/loadAllStateBtCommand", None),
		"loadStateTypeList": (PNAME, "motor-state/loadStateTypeList", None),
		"loadBtErrorCommand": (PNAME, "motor-state/loadBtErrorCommand", None),
		"myMotorList": (PNAME, "motor/myMotorList", None),
		"myActiveMotor": (PNAME, "motor/myActiveMotor", None),
	}
	for name, (prefix, endpoint, params) in fetches.items():
		try:
			env = _get(prefix, endpoint, token, args.timeout, params)
			results[name] = env
			code = env.get("code")
			data = env.get("data")
			n = len(data) if isinstance(data, list) else ("obj" if data else "none")
			print(f"{name}: code={code} data={n}", file=sys.stderr)
		except Exception as exc:  # noqa: BLE001
			results[name] = {"error": str(exc)}
			print(f"{name}: ERROR {exc}", file=sys.stderr)

	with open(args.out, "w", encoding="utf-8") as handle:
		json.dump(results, handle, indent=2, ensure_ascii=False)
	print(f"\nSaved to {args.out}", file=sys.stderr)

	# Pretty-print the command list so it's obvious whether we got it.
	cmds = (results.get("loadAllStateBtCommand") or {}).get("data")
	if isinstance(cmds, list) and cmds:
		print(f"\nGot {len(cmds)} commands:")
		for c in cmds:
			print(f"  stateCode={c.get('stateCode')!r:>28}  btReadCommand={c.get('btReadCommand')!r}")
		print("\nNext: python capture.py --name <serial> --commands " + args.out + " --soc <dash %>")
	else:
		print(
			"\nNo command list came back. If you have more than one bike, the server may key the "
			"list to your ACTIVE bike — open the app, make sure the right bike is selected, then "
			"re-run. (myActiveMotor/myMotorList in the saved file show what the account sees.)",
			file=sys.stderr,
		)
	return 0


if __name__ == "__main__":
	sys.exit(main())
