"""Transfer verified v2A history into a new v2B journal."""

from .local_cell_transfer import V2A_TO_V2B, TransferError, transfer_cli, transfer_journal

SCHEMA = V2A_TO_V2B.schema
SOURCE_SCHEMA = V2A_TO_V2B.source_schema
TARGET_SCHEMA = V2A_TO_V2B.target_schema
MAX_TRANSFER_TICKS = V2A_TO_V2B.max_ticks


def transfer_v2a_to_v2b(
        source, destination, *, expected_source_head, seed, graph,
        create_new_journal=False, max_ticks=MAX_TRANSFER_TICKS):
    """Re-express the complete frame history and preserve the original file."""
    return transfer_journal(
        source, destination, transfer=V2A_TO_V2B, expected_source_head=expected_source_head,
        seed=seed, graph=graph, create_new_journal=create_new_journal, max_ticks=max_ticks,
    )


def main(argv=None):
    return transfer_cli(V2A_TO_V2B, argv)


if __name__ == "__main__":
    raise SystemExit(main())
