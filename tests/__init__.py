"""Test package.

Present so `python -m unittest discover -s tests -t .` can import these as
`tests.*` and, through that, reach `scripts.vault_parser` from the project
root. Without it discovery adds `tests/` itself to the path and the parser
imports fail.
"""
