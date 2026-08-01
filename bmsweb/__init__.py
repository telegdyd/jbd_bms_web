"""Self-hosted browser for recordings made by the BMS Android app."""

#: Bumped whenever parsing or summary logic changes in a way that alters stored figures. Sessions
#: are stamped with it, and anything older is rebuilt from its raw CSV by `bmsctl reparse`.
SCHEMA_VERSION = 1
