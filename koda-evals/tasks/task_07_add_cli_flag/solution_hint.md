# Solution

```python
parser.add_argument("-l", "--lines", action="store_true",
                    help="print line count instead of word count")
args = parser.parse_args(argv)

if args.lines:
    n = count_lines(args.path)
    print(f"line count of {args.path}: {n}")
else:
    n = count_words(args.path)
    print(f"word count of {args.path}: {n}")
```
