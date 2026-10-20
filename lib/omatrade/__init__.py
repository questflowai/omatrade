"""Omatrade: a thin Omarchy desktop client for a Questflow account.

Everything the plugin shows comes from one configurable Questflow API base
URL. Signal matching and risk monitoring run on the server; this package
signs in with OAuth (Authorization Code + PKCE), keeps tokens in the system
keyring, holds one server-sent-events stream open from a background daemon,
and turns what arrives into a local snapshot the bar reads and desktop
notifications the user can act on.
"""

__version__ = "0.3.0"
