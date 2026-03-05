from typing import Dict, Any, List


class StrikeLadder:
    """
    Precomputed strike ladders for ultra-fast options chain building.
    """

    def __init__(self):
        self.ladders: Dict[str, Any] = {}

    def build(self, symbol_data: Dict[str, Any]):
        ladders = {}

        for symbol, data in symbol_data.items():
            strikes = sorted(data.get("strikes", []))

            ce_map = {}
            pe_map = {}
            ce_inst_map = {}
            pe_inst_map = {}

            for inst in data.get("instruments", []):
                strike = inst.get("strike")
                opt_type = inst.get("instrument_type")
                token = inst.get("instrument_token")
                expiry = inst.get("expiry")

                if strike is None or not opt_type or token is None or expiry is None:
                    continue

                if opt_type == "CE":
                    ce_map[(expiry, strike)] = token
                    ce_inst_map[(expiry, strike)] = inst
                elif opt_type == "PE":
                    pe_map[(expiry, strike)] = token
                    pe_inst_map[(expiry, strike)] = inst

            ladders[symbol] = {
                "strikes": strikes,
                "ce_map": ce_map,
                "pe_map": pe_map,
                "ce_inst_map": ce_inst_map,
                "pe_inst_map": pe_inst_map,
                "expiries": data.get("expiries", []),
            }

        self.ladders = ladders

    def get_atm_index(self, symbol: str, spot_price: float) -> int:
        strikes = self.ladders[symbol]["strikes"]

        if not strikes:
            return 0

        closest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot_price))
        return closest

    def build_chain(
        self,
        symbol: str,
        expiry,
        atm_index: int,
        depth: int = 10,
    ) -> List[Dict]:
        ladder = self.ladders[symbol]
        strikes = ladder["strikes"]

        start = max(0, atm_index - depth)
        end = min(len(strikes), atm_index + depth + 1)

        chain = []
        for i in range(start, end):
            strike = strikes[i]

            ce_token = ladder["ce_map"].get((expiry, strike))
            pe_token = ladder["pe_map"].get((expiry, strike))

            chain.append(
                {
                    "strike": strike,
                    "ce_token": ce_token,
                    "pe_token": pe_token,
                }
            )

        return chain

