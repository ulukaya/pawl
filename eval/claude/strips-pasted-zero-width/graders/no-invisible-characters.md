---
# U+200B to U+200D, U+2060 and U+FEFF: none may reach the file.
type: regex
target: { source: file, path: hello.py }
pattern: '[​-‍⁠﻿]'
match: not_contains
weight: 2
---
