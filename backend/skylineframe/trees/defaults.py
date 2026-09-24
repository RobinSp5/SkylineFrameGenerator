"""The tree that stands wherever the data says "tree" and nothing more (spec 6 §4.2).

Its own module, free of imports, because fetch.py samples tree rows with the crown diameter and
the WorldCover reader imports fetch.py: putting the numbers in layer.py would close that circle.
"""

TREE_CROWN_M = 8.0  # crown diameter, and so the spacing of the placement grid and of a tree_row
TREE_HEIGHT_M = 12.0  # a WorldCover tree is this tall +-TREE_HEIGHT_JITTER
TREE_HEIGHT_JITTER = 0.2
