"""Transfer verified v2B history into a new v3 journal."""

from .local_cell_transfer import V2B_TO_V3, TransferError, transfer_cli, transfer_journal

SCHEMA = V2B_TO_V3.schema
SOURCE_SCHEMA = V2B_TO_V3.source_schema
TARGET_SCHEMA = V2B_TO_V3.target_schema
MAX_TRANSFER_TICKS = V2B_TO_V3.max_ticks


def transfer_v2b_to_v3(
        source, destination, *, expected_source_head, seed, graph,
        create_new_journal=False, max_ticks=MAX_TRANSFER_TICKS):
    """Re-express the complete frame history and preserve the original file."""
    return transfer_journal(
        source, destination, transfer=V2B_TO_V3, expected_source_head=expected_source_head,
        seed=seed, graph=graph, create_new_journal=create_new_journal, max_ticks=max_ticks,
    )


def main(argv=None):
    return transfer_cli(V2B_TO_V3, argv)


if __name__ == "__main__":
    raise SystemExit(main())
