class OllamaTorError(RuntimeError):
    """Base class for errors that are safe to show at the command line."""


class SocksProtocolError(OllamaTorError):
    """The configured listener did not complete a valid SOCKS5 connection."""


class TorUnavailable(OllamaTorError):
    """No configured SOCKS listener was verified as Tor."""
