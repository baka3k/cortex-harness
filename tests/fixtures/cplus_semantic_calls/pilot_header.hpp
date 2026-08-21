#pragma once

inline int header_target(int value) { return value + 1; }
inline int header_entry(int value) { return header_target(value); }
