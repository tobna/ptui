# Keys

Every binding ptui ships with, generated from `src/ptui/defaults/keys.toml`
and the command registry — run `uv run python scripts/keydoc.py` after changing
either. Your `$XDG_CONFIG_HOME/papis/ptui/keys.toml` holds only your changes,
laid over these key by key. Each of your bindings drops every shipped one it
collides with in the modes it reaches — the same key, a chord either is a prefix
of, or the mode's only key for the same command and args — so swapping two keys
takes two lines. A command with several keys (`o` and `enter`) keeps them all as
aliases. `"o" = false` unbinds a key, and a whole namespace for a prefix.

`escape` always leaves the current mode and `?` always opens the help, in every
mode, whatever the tables below say — the dispatcher guarantees both so no pane
can trap the keyboard.

Commands marked *(not implemented)* are bound on purpose: they log a notice
instead of doing anything, and the binding is already in place for when they land.

## `[global]`

Every pane — list, info, files and log — has these as well, unless its own table binds the same key or a chord sharing its prefix (a pane's `g` drops `g o` there). A picker does not: its filter box has the keyboard.

| Keys | Command | Does what |
| --- | --- | --- |
| `1` | `pane.focus` pane=list | focus a pane |
| `2` | `pane.focus` pane=info | focus a pane |
| `3` | `pane.focus` pane=files | focus a pane |
| `4` | `pane.focus` pane=log | focus a pane |
| `:` | `cmdline.open` | run a command by name |
| `ctrl+p` | `cmdline.open` | run a command by name |
| `ctrl+r` | `app.redo` | redo |
| `g o` | `app.log` | operation log |
| `h` | `pane.cycle` back=True | previous pane |
| `l` | `pane.cycle` | next pane |
| `q` | `app.quit` | quit |
| `shift+tab` | `pane.cycle` back=True | next pane |
| `tab` | `pane.cycle` | next pane |
| `u` | `app.undo` | undo |

## `[modes.list]`

The document list — the mode ptui starts in, and the one every other mode falls back to.

| Keys | Command | Does what |
| --- | --- | --- |
| `! !` | `doctor.run` | scan + narrow to findings |
| `! a` | `doctor.fix` | fix findings on marked/shown |
| `! d` | `doctor.run` current=True | re-check this document |
| `! f` | `doctor.fix` current=True | fix findings here |
| `! o` | `doctor.fix_pick` | fix one finding… |
| `/` | `query.narrow` | narrow (instant) |
| `?` | `help.show` | help |
| `\ c` | `app.config_check` | check config |
| `\ k` | `keymap.check` | check keymap conflicts |
| `\ s` | `query.save` | save current search |
| `\ t` | `theme.picker` | theme |
| `a` | `doc.add` | add document |
| `b` | `doc.browse` | open URL/DOI |
| `c T` | `doc.untag` | remove tags |
| `c f` | `doc.set` | set any field |
| `c r` | `doc.rating` | rating |
| `c s` | `doc.status` | reading status |
| `c t` | `doc.tag` | add tags |
| `c u` | `doc.refresh` | update from DOI / arXiv / Crossref |
| `ctrl+a` | `mark.all_filtered` | mark all (alias of m a) |
| `ctrl+d` | `nav.page_down` | page down |
| `ctrl+s` | `sort.reverse` | reverse sort |
| `ctrl+u` | `nav.page_up` | page up |
| `d d` | `doc.delete` | delete document |
| `down` | `nav.down` | move down |
| `E` | `doc.edit` other=True | edit, the other way |
| `e` | `doc.edit` | edit (per edit.mode) |
| `enter` | `doc.open` | open file |
| `escape` | `app.escape` | cancel |
| `f R` | `files.relocate` force=True | relocate (force) |
| `f a` | `files.attach` | attach file |
| `f n` | `files.normalize` | normalize path styles |
| `f o` | `files.open_pick` | open which file… |
| `f r` | `files.relocate` | relocate + rename to scheme |
| `G` | `nav.bottom` | last document |
| `g g` | `nav.top` | top |
| `g l` | `lib.switch` | switch library |
| `g m` | `mark.show_only` | marked only |
| `g n` | `doc.notes` | notes |
| `g r` | `nav.goto` | go to a citekey |
| `g s` | `query.load` | saved searches |
| `i` | `doc.add` source=inbox | add from inbox |
| `J` | `info.scroll` | scroll info down |
| `j` | `nav.down` | move down |
| `K` | `info.scroll` up=True | scroll info up |
| `k` | `nav.up` | move up |
| `m A` | `mark.all_filtered` unmark=True | unmark all filtered |
| `m Q` | `mark.query` unmark=True | unmark by query |
| `m a` | `mark.all_filtered` | mark all filtered |
| `m c` | `mark.clear` | clear marks |
| `m i` | `mark.invert` | invert marks |
| `m m` | `doc.merge` | merge marked documents |
| `m o` | `mark.show_only` | toggle marked-only |
| `m q` | `mark.query` | mark by query |
| `O` | `doc.open_folder` | open folder |
| `o` | `doc.open` | open file |
| `r` | `app.reload` | reload library |
| `S` | `sort.picker` | sort by… |
| `s` | `query.scope` | search (papis query) |
| `space` | `mark.toggle` | mark/unmark |
| `up` | `nav.up` | move up |
| `V` | `visual.start` unmark=True | visual unmark |
| `v` | `visual.start` | visual mark |
| `y b` | `export.bibtex` | bibtex |
| `y e` | `export.as` | any papis format |
| `y p` | `export.path` | absolute path |
| `y u` | `export.url` | DOI/URL |
| `y y` | `export.citekey` | \cite{ref} |
| `z +` | `pane.resize` delta=0.05 | adjust the split |
| `z -` | `pane.resize` delta=-0.05 | adjust the split |
| `z f` | `pane.toggle` pane=files | show/hide a pane |
| `z i` | `pane.toggle` pane=info | show/hide a pane |
| `z z` | `pane.toggle_layout` | horizontal/vertical |

## `[modes.files]`

The files pane, focused with `3`: the file verbs act on the entry under its cursor.

| Keys | Command | Does what |
| --- | --- | --- |
| `down` | `nav.down` | move down |
| `enter` | `doc.open` | open this file |
| `J` | `files.reorder` to=1 | move a file up or down |
| `j` | `nav.down` | move down |
| `K` | `files.reorder` to=-1 | move a file up or down |
| `k` | `nav.up` | move up |
| `m` | `files.relocate` | apply placement rules |
| `o` | `doc.open` | open this file |
| `p` | `files.repoint` | fix broken path |
| `r` | `files.rename` | rename to scheme |
| `up` | `nav.up` | move up |
| `x` | `files.detach` | remove from list, keep file |
| `z f` | `pane.toggle` pane=files | close the files pane |

## `[modes.info]`

The info pane, focused with `2` or `tab`.

| Keys | Command | Does what |
| --- | --- | --- |
| `ctrl+d` | `info.scroll` | scroll the info pane |
| `ctrl+u` | `info.scroll` up=True | scroll the info pane |
| `e` | `doc.edit` | edit |
| `J` | `info.scroll` | scroll the info pane |
| `j` | `nav.down` | move down |
| `K` | `info.scroll` up=True | scroll the info pane |
| `k` | `nav.up` | move up |

## `[modes.log]`

The log pane, focused with `4` or `g o`.

| Keys | Command | Does what |
| --- | --- | --- |
| `ctrl+d` | `log.scroll` | scroll the log |
| `ctrl+u` | `log.scroll` up=True | scroll the log |
| `G` | `log.scroll` end=True | bottom of the log |
| `g g` | `log.scroll` up=True end=True | top of the log |
| `j` | `log.scroll` lines=1 | scroll down a line |
| `k` | `log.scroll` up=True lines=1 | scroll up a line |

## `[modes.picker]`

Any modal picker (`S`, `f o`, `g l`, `?`). The filter box has focus, so motions cannot be letters.

| Keys | Command | Does what |
| --- | --- | --- |
| `ctrl+n` | `nav.down` | move down |
| `ctrl+p` | `nav.up` | move up |
| `down` | `nav.down` | move down |
| `enter` | `picker.confirm` | confirm |
| `escape` | `picker.cancel` | cancel |
| `shift+enter` | `picker.confirm` invert=True | confirm |
| `up` | `nav.up` | move up |

## `[options]`

| Option | Default | Does what |
| --- | --- | --- |
| `which_key` | `true` | Show the bindings under a prefix after a pause. |
| `which_key_delay_ms` | `400` | How long that pause is. |
| `leader` | `"\\"` | The leader key for rare and administrative commands. |
| `hint_bar` | `true` | Show context-relevant bindings on the bottom row. |
| `hint_bar_max` | `6` | How many hints fit there. |
| `show_keys_in_cmdline` | `true` | Show each command's binding beside it in the `:` command line. |
| `escape_chain` | `["modal", "visual", "narrow"]` | What `escape` cancels in the list mode, first applicable wins. Outside the list mode escape always returns to the list. |
