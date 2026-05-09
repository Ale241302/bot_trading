"""
csm_pair_specs.py
Especificaciones de los 30 instrumentos usados por la estrategia CSM
("Francotirador de Fuerza Relativa con Compounding Agresivo").

Cobertura:
  - 28 cruces de las 8 majors: USD, EUR, GBP, JPY, CHF, CAD, AUD, NZD
  - XAUUSD (oro) y XAGUSD (plata) → permiten que XAU/XAG entren al ranking
    de fuerza, pero el bot solo opera el oro/plata cuando el ranking marque
    XAU o XAG como divisa más fuerte/débil.

Cada spec tiene:
  - base / quote      : divisas que componen el cruce (para CSM scoring)
  - pip_size          : 0.0001 (FX standard) | 0.01 (JPY) | 0.01 (XAU) | 0.001 (XAG)
  - pip_value_per_lot : USD por pip por lote estándar (referencial; el broker dicta el real)
  - yf_symbol         : ticker yfinance

CURRENCIES y PAIR_AVAILABILITY se exponen para el cálculo de fuerza.
"""

# ───────────────────────── divisas en el universo ──────────────────────────
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "XAU", "XAG"]


# ───────────────────────── specs por instrumento ───────────────────────────
# Convención: el `symbol` interno (clave del dict) es siempre BASE+QUOTE.
# pip_value_per_lot es referencial — varía con el tipo de cuenta.
CSM_PAIR_SPECS: dict[str, dict] = {
    # ─── USD majors ───
    "EURUSD": {"base": "EUR", "quote": "USD", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "EURUSD=X"},
    "GBPUSD": {"base": "GBP", "quote": "USD", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "GBPUSD=X"},
    "AUDUSD": {"base": "AUD", "quote": "USD", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "AUDUSD=X"},
    "NZDUSD": {"base": "NZD", "quote": "USD", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "NZDUSD=X"},
    "USDJPY": {"base": "USD", "quote": "JPY", "pip_size": 0.01,   "pip_value_per_lot": 6.50,  "yf_symbol": "JPY=X"},
    "USDCHF": {"base": "USD", "quote": "CHF", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "CHF=X"},
    "USDCAD": {"base": "USD", "quote": "CAD", "pip_size": 0.0001, "pip_value_per_lot": 7.00,  "yf_symbol": "CAD=X"},

    # ─── EUR crosses ───
    "EURGBP": {"base": "EUR", "quote": "GBP", "pip_size": 0.0001, "pip_value_per_lot": 12.5,  "yf_symbol": "EURGBP=X"},
    "EURJPY": {"base": "EUR", "quote": "JPY", "pip_size": 0.01,   "pip_value_per_lot": 6.50,  "yf_symbol": "EURJPY=X"},
    "EURCHF": {"base": "EUR", "quote": "CHF", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "EURCHF=X"},
    "EURCAD": {"base": "EUR", "quote": "CAD", "pip_size": 0.0001, "pip_value_per_lot": 7.00,  "yf_symbol": "EURCAD=X"},
    "EURAUD": {"base": "EUR", "quote": "AUD", "pip_size": 0.0001, "pip_value_per_lot": 6.50,  "yf_symbol": "EURAUD=X"},
    "EURNZD": {"base": "EUR", "quote": "NZD", "pip_size": 0.0001, "pip_value_per_lot": 6.00,  "yf_symbol": "EURNZD=X"},

    # ─── GBP crosses ───
    "GBPJPY": {"base": "GBP", "quote": "JPY", "pip_size": 0.01,   "pip_value_per_lot": 6.50,  "yf_symbol": "GBPJPY=X"},
    "GBPCHF": {"base": "GBP", "quote": "CHF", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "GBPCHF=X"},
    "GBPCAD": {"base": "GBP", "quote": "CAD", "pip_size": 0.0001, "pip_value_per_lot": 7.00,  "yf_symbol": "GBPCAD=X"},
    "GBPAUD": {"base": "GBP", "quote": "AUD", "pip_size": 0.0001, "pip_value_per_lot": 6.50,  "yf_symbol": "GBPAUD=X"},
    "GBPNZD": {"base": "GBP", "quote": "NZD", "pip_size": 0.0001, "pip_value_per_lot": 6.00,  "yf_symbol": "GBPNZD=X"},

    # ─── AUD crosses ───
    "AUDJPY": {"base": "AUD", "quote": "JPY", "pip_size": 0.01,   "pip_value_per_lot": 6.50,  "yf_symbol": "AUDJPY=X"},
    "AUDCHF": {"base": "AUD", "quote": "CHF", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "AUDCHF=X"},
    "AUDCAD": {"base": "AUD", "quote": "CAD", "pip_size": 0.0001, "pip_value_per_lot": 7.00,  "yf_symbol": "AUDCAD=X"},
    "AUDNZD": {"base": "AUD", "quote": "NZD", "pip_size": 0.0001, "pip_value_per_lot": 6.00,  "yf_symbol": "AUDNZD=X"},

    # ─── NZD crosses ───
    "NZDJPY": {"base": "NZD", "quote": "JPY", "pip_size": 0.01,   "pip_value_per_lot": 6.50,  "yf_symbol": "NZDJPY=X"},
    "NZDCHF": {"base": "NZD", "quote": "CHF", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "NZDCHF=X"},
    "NZDCAD": {"base": "NZD", "quote": "CAD", "pip_size": 0.0001, "pip_value_per_lot": 7.00,  "yf_symbol": "NZDCAD=X"},

    # ─── CAD crosses ───
    "CADJPY": {"base": "CAD", "quote": "JPY", "pip_size": 0.01,   "pip_value_per_lot": 6.50,  "yf_symbol": "CADJPY=X"},
    "CADCHF": {"base": "CAD", "quote": "CHF", "pip_size": 0.0001, "pip_value_per_lot": 10.0, "yf_symbol": "CADCHF=X"},

    # ─── CHF crosses ───
    "CHFJPY": {"base": "CHF", "quote": "JPY", "pip_size": 0.01,   "pip_value_per_lot": 6.50,  "yf_symbol": "CHFJPY=X"},

    # ─── Metales (cotizados contra USD) ───
    # XAUUSD: 1 pip = 0.01 USD; lote estándar 100 oz → $1/pip/lote.
    # XAGUSD: 1 pip = 0.001 USD; lote estándar 5000 oz → $5/pip/lote (aprox).
    "XAUUSD": {"base": "XAU", "quote": "USD", "pip_size": 0.01,   "pip_value_per_lot": 1.0,   "yf_symbol": "GC=F"},
    "XAGUSD": {"base": "XAG", "quote": "USD", "pip_size": 0.001,  "pip_value_per_lot": 5.0,   "yf_symbol": "SI=F"},
}


# Pares disponibles en el universo (orden estable para iteración)
CSM_DEFAULT_PAIRS = list(CSM_PAIR_SPECS.keys())


def get_pairs_for_currency(currency: str) -> list[tuple[str, str]]:
    """
    Retorna lista de (symbol, role) donde role es 'base' o 'quote' según la
    posición de `currency` en el cruce. Útil para calcular la fuerza de la
    divisa sumando los cambios % en todos los cruces que la incluyen.
    """
    out = []
    for sym, spec in CSM_PAIR_SPECS.items():
        if spec["base"] == currency:
            out.append((sym, "base"))
        elif spec["quote"] == currency:
            out.append((sym, "quote"))
    return out


def find_pair(base: str, quote: str) -> tuple[str, int] | None:
    """
    Busca el cruce que contiene `base` y `quote`.
    Retorna (symbol, direction) donde direction es:
       +1 si el orden coincide (base→quote en el broker)
       -1 si el cruce existe pero invertido (broker tiene quote→base)
    None si ningún cruce los conecta directamente.
    """
    if base == quote:
        return None
    for sym, spec in CSM_PAIR_SPECS.items():
        if spec["base"] == base and spec["quote"] == quote:
            return (sym, +1)
        if spec["base"] == quote and spec["quote"] == base:
            return (sym, -1)
    return None
