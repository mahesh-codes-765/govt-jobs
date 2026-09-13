from .tgpsc import TGPSCAdapter
from .appsc import APPSCAdapter
from .rrb_secunderabad import RRBSecunderabadAdapter
from .ibps import IBPSAdapter
from .drdo import DRDOAdapter
from .isro import ISROAdapter

ADAPTERS = {
    "tgpsc": TGPSCAdapter,
    "appsc": APPSCAdapter,
    "rrb_secunderabad": RRBSecunderabadAdapter,
    "ibps": IBPSAdapter,
    "drdo": DRDOAdapter,
    "isro": ISROAdapter,
}

def get_adapter(key: str):
    if key not in ADAPTERS:
        raise ValueError(f"Unknown source adapter: {key}. Available: {', '.join(sorted(ADAPTERS))}")
    return ADAPTERS[key]()
