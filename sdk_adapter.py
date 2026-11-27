# SDK v1 adapter appended to the unchanged GENESIS source in a release bundle.
# Pure projection: restore from explicit ledger data, never mutate external state.
def _project_ledger(inputs, config, tick, time_ns):
    if type(config) is not dict or "ledger" not in config or type(config["ledger"]) is not dict:
        raise GenesisError("genesis-ledger.project requires config.ledger snapshot")
    ledger = GenomeLedger.from_snapshot(config["ledger"])
    return {"phenotype": ledger.phenotype(tick), "ledger": ledger.snapshot()}, []

HANDLERS = {"genesis-ledger.project": _project_ledger}
