"""
Print the permanent call link for a business.

    uv run python -m scripts.call_link --base https://your-public-url.example
    uv run python -m scripts.call_link --base https://your-public-url.example --tenant ten_abc123

`--tenant default` (the default) is the business defined in .env while no businesses exist.
The link never expires and anyone who has it can start a call, so share it carefully. Raise
CALL_LINK_VERSION in .env and restart to turn every permanent link off at once.
"""

import argparse

from core.signed_links import build_permanent_link


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="the public base URL, e.g. https://abc.trycloudflare.com")
    parser.add_argument("--tenant", default="default", help='a business id, or "default"')
    args = parser.parse_args()
    print(build_permanent_link(args.base, args.tenant))


if __name__ == "__main__":
    main()
