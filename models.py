from dataclasses import dataclass
from typing import List


@dataclass
class PlacementGroup:
    """One paired row from the DESCRIPTION/USAGE panes.

    Attributes:
        description:       The component description string.
        components:        All expanded designator strings for this row.
        source_line_index: 0-based index of the originating text-pane line,
                           used for highlight and scroll-to operations.
    """

    description: str
    components: List[str]
    source_line_index: int
