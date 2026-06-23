# Sample Output

Janitor renders everything with [Rich](https://github.com/Textualize/rich) —
colorized tables, panels, spinners, and progress bars. The captures below are
plain-text renderings; in a real terminal they are fully colorized.

## `jt doctor`

```text
                                 Janitor Doctor
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Component    ┃   Status   ┃ Version / Detail                                 ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Python       │    ✓ ok    │ 3.14.0                                           │
│ uv           │    ✓ ok    │ uv 0.11.6                                        │
│ Docker       │    ✓ ok    │ 29.5.2                                           │
│ Homebrew     │    ✓ ok    │ Homebrew 6.0.3                                   │
│ Kubernetes   │    ✓ ok    │ lke560651-ctx                                    │
│ Supabase CLI │    ✓ ok    │ 2.107.0                                          │
│ Disk (/)     │ 64.6% used │ 80.8 GB free of 228.3 GB                         │
└──────────────┴────────────┴──────────────────────────────────────────────────┘
╭────────────────────────────────── Summary ───────────────────────────────────╮
│ All systems healthy.                                                          │
╰───────────────────────────────────────────────────────────────────────────────╯
```

The Status column is green for healthy components, yellow when a tool is missing
or degraded, and red for critical problems. The disk row turns yellow above 75%
and red above 90%.

## `jt docker status`

```text
                     Docker Disk Usage
┏━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Type          ┃ Total ┃ Active ┃     Size ┃ Reclaimable ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Images        │    11 │     11 │  12.5 GB │      3.7 GB │
│ Containers    │    11 │     11 │  25.6 MB │         0 B │
│ Local Volumes │     9 │      3 │ 225.9 MB │    134.2 MB │
│ Build Cache   │     0 │      0 │      0 B │         0 B │
└───────────────┴───────┴────────┴──────────┴─────────────┘
Total reclaimable: 3.8 GB
```

## `jt disk usage /`

```text
              Disk Usage — /
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃    Total ┃     Used ┃    Free ┃ Used % ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ 228.3 GB │ 147.4 GB │ 80.8 GB │  64.6% │
└──────────┴──────────┴─────────┴────────┘
```

## `jt --dry-run docker prune`

```text
About to run a safe prune (~3.8 GB reclaimable).
Dry-run: no changes will be made.
would run: docker system prune --force
would run: docker builder prune --force
Prune complete.
```

> 💡 **Tip:** capture true-color SVGs for your own README with
> `jt doctor | rich --export-svg doctor.svg` (requires `rich-cli`).
