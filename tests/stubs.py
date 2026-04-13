import sys
import types


def install_dependency_stubs() -> None:
    if "psycopg" not in sys.modules:
        psycopg_module = types.ModuleType("psycopg")
        psycopg_module.connect = _unsupported_connect
        sys.modules["psycopg"] = psycopg_module

        psycopg_types_module = types.ModuleType("psycopg.types")
        sys.modules["psycopg.types"] = psycopg_types_module

        psycopg_json_module = types.ModuleType("psycopg.types.json")

        class Jsonb:
            def __init__(self, value):
                self.value = value

        psycopg_json_module.Jsonb = Jsonb
        sys.modules["psycopg.types.json"] = psycopg_json_module

    if "bs4" not in sys.modules:
        bs4_module = types.ModuleType("bs4")

        class BeautifulSoup:
            def __init__(self, html: str, parser: str) -> None:
                self._html = html

            def get_text(self, separator: str, strip: bool = False) -> str:
                return self._html

        bs4_module.BeautifulSoup = BeautifulSoup
        sys.modules["bs4"] = bs4_module

    if "google.auth.exceptions" not in sys.modules:
        google_module = sys.modules.setdefault("google", types.ModuleType("google"))
        auth_module = types.ModuleType("google.auth")
        google_module.auth = auth_module
        sys.modules["google.auth"] = auth_module

        exceptions_module = types.ModuleType("google.auth.exceptions")

        class RefreshError(Exception):
            def __init__(self, message: str = "", retryable: bool = False) -> None:
                super().__init__(message)
                self.retryable = retryable

        exceptions_module.RefreshError = RefreshError
        auth_module.exceptions = exceptions_module
        sys.modules["google.auth.exceptions"] = exceptions_module

    if "google.oauth2.credentials" not in sys.modules:
        oauth2_module = types.ModuleType("google.oauth2")
        credentials_module = types.ModuleType("google.oauth2.credentials")

        class Credentials:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs

        credentials_module.Credentials = Credentials
        oauth2_module.credentials = credentials_module
        sys.modules["google.oauth2"] = oauth2_module
        sys.modules["google.oauth2.credentials"] = credentials_module

    if "googleapiclient.errors" not in sys.modules:
        googleapiclient_module = types.ModuleType("googleapiclient")
        errors_module = types.ModuleType("googleapiclient.errors")

        class HttpError(Exception):
            def __init__(self, resp, content, uri: str = "") -> None:
                super().__init__(content.decode("utf-8", errors="replace"))
                self.resp = resp
                self.content = content
                self.uri = uri

        errors_module.HttpError = HttpError
        googleapiclient_module.errors = errors_module
        sys.modules["googleapiclient"] = googleapiclient_module
        sys.modules["googleapiclient.errors"] = errors_module

    if "googleapiclient.discovery" not in sys.modules:
        discovery_module = types.ModuleType("googleapiclient.discovery")

        def build(*args, **kwargs):
            raise NotImplementedError("googleapiclient discovery build is stubbed in tests")

        discovery_module.build = build
        sys.modules["googleapiclient.discovery"] = discovery_module


def _unsupported_connect(*args, **kwargs):
    raise NotImplementedError("psycopg is stubbed in tests")
