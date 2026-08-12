# Keys

Every binding ptui ships with, generated from `src/ptui/defaults/keys.toml`
and the command registry — run `uv run python scripts/keydoc.py` after changing
either. Copy the file to `$XDG_CONFIG_HOME/papis/ptui/keys.toml` to override:
a mode you define replaces ours wholesale, so a shipped binding can be removed.

`escape` always leaves the current mode and `?` always opens the help, in every
mode, whatever the tables below say — the dispatcher guarantees both so no pane
can trap the keyboard.

Commands marked *(not implemented)* are bound on purpose: they log a notice
instead of doing anything, and the binding is already in place for when they land.

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
| `1` | `pane.focus` pane=list | focus a pane |
| `2` | `pane.focus` pane=info | focus a pane |
| `3` | `pane.focus` pane=files | focus a pane |
| `4` | `pane.focus` pane=log | focus a pane |
| `:` | `cmdline.open` | run a command by name |
| `?` | `help.show` | help |
| `\ c` | `app.config_check` | check config |
| `\ k` | `keymap.check` | check keymap conflicts |
| `\ s` | `query.save` | save current search *(not implemented)* |
| `\ t` | `theme.picker` | theme |
| `a` | `doc.add` | add document |
| `b` | `doc.browse` | open URL/DOI |
| `c T` | `doc.untag` | remove tags |
| `c f` | `doc.set` | set any field |
| `c r` | `doc.rating` | rating |
| `c s` | `doc.status` | reading status |
| `c t` | `doc.tag` | add tags |
| `ctrl+a` | `mark.all_filtered` | mark all (alias of m a) |
| `ctrl+d` | `nav.page_down` | page down |
| `ctrl+p` | `cmdline.open` | run a command by name |
| `ctrl+r` | `app.redo` | — *(not implemented)* |
| `ctrl+s` | `sort.reverse` | reverse sort |
| `ctrl+u` | `nav.page_up` | page up |
| `d d` | `doc.delete` | delete document *(not implemented)* |
| `down` | `nav.down` | move down |
| `E` | `doc.edit_raw` | edit info.yaml in $EDITOR |
| `e` | `doc.edit` | edit (structured) |
| `enter` | `doc.open` | open file |
| `escape` | `app.escape` | cancel |
| `f R` | `files.relocate` force=True | relocate (force) |
| `f a` | `files.attach` | attach file *(not implemented)* |
| `f n` | `files.normalize` | normalize path styles *(not implemented)* |
| `f o` | `files.open_pick` | open which file… |
| `f r` | `files.relocate` | relocate + rename to scheme |
| `G` | `nav.bottom` | last document |
| `g g` | `nav.top` | top |
| `g l` | `lib.switch` | switch library |
| `g m` | `view.marked` | marked only *(not implemented)* |
| `g n` | `doc.notes` | notes |
| `g o` | `app.log` | operation log |
| `g s` | `view.saved` | saved searches *(not implemented)* |
| `i` | `doc.add` source=inbox | add from inbox |
| `j` | `nav.down` | move down |
| `k` | `nav.up` | move up |
| `m a` | `mark.all_filtered` | mark all filtered |
| `m c` | `mark.clear` | clear marks |
| `m i` | `mark.invert` | invert marks |
| `m m` | `doc.merge` | merge marked documents |
| `m o` | `mark.show_only` | toggle marked-only |
| `O` | `doc.open_folder` | open folder |
| `o` | `doc.open` | open file |
| `q` | `app.quit` | quit |
| `r` | `app.reload` | reload library |
| `S` | `sort.picker` | sort by… |
| `s` | `query.scope` | search (papis query) |
| `shift+tab` | `pane.cycle` back=True | next pane |
| `space` | `mark.toggle` | mark/unmark |
| `tab` | `pane.cycle` | next pane |
| `u` | `app.undo` | — *(not implemented)* |
| `up` | `nav.up` | move up |
| `V` | `visual.line` | — *(not implemented)* |
| `v` | `visual.start` | — *(not implemented)* |
| `y b` | `export.bibtex` | bibtex |
| `y p` | `export.path` | absolute path |
| `y u` | `export.url` | DOI/URL |
| `y y` | `export.citekey` | \cite{ref} |
| `z +` | `pane.resize` delta=0.05 | adjust the split |
| `z -` | `pane.resize` delta=-0.05 | adjust the split |
| `z f` | `pane.toggle` pane=files | show/hide a pane |
| `z i` | `pane.toggle` pane=info | show/hide a pane |
| `z z` | `pane.toggle_layout` | horizontal/vertical |

## `[modes.files]`

The files pane. Not built yet, so these bindings only log.

| Keys | Command | Does what |
| --- | --- | --- |
| `enter` | `doc.open` | open file |
| `J` | `files.reorder` to=1 | — *(not implemented)* |
| `j` | `nav.down` | move down |
| `K` | `files.reorder` to=-1 | — *(not implemented)* |
| `k` | `nav.up` | move up |
| `m` | `files.relocate` | move to pdf_root |
| `p` | `files.repoint` | fix broken path *(not implemented)* |
| `r` | `files.rename` | rename to scheme *(not implemented)* |
| `x` | `files.detach` | remove from list, keep file *(not implemented)* |

## `[modes.info]`

The info pane, focused with `2` or `tab`.

| Keys | Command | Does what |
| --- | --- | --- |
| `e` | `doc.edit` | edit |
| `j` | `nav.down` | move down |
| `k` | `nav.up` | move up |

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
