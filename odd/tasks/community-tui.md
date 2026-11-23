# Community TUI refinements
Refine the player and settings in the community checkout only.

## Specs
S1. “este boton esta genial en funcionalidad pero no muestra a simple vista que hace” — header command palette.
S2. “este boton esta de mas” — standalone Playlist.
S3. “esta barra no tiene el mismo look and feel del resto de la aplicacion” — command strip.
S4. “se repite todo mucho” — deduplicate actions between strip and footer without removing shortcuts.
S5. “el cancel es la unica manera de cerrarlo, deberia ser un save and close o solo close o algo por el estilo, revisa este modal en su totalidad” — persist and dismiss on successful save; retain discard.
S6. “el compact layout solo cambia el color de la app no hace mas nada” — demonstrable geometry changes at fixed terminal size.

S7. “esta muy pegado a nivel de que las opciones de la derecha parecen ser una unica caja, controla eso por favor, misma caja de tamaño para todos” — separate all right-column fields uniformly.

## Tasks
T1 | S1–S4,S6 | player chrome, real compact geometry, tests, help | done | 44190dc; integrated suite: 767 passed, 56 subtests passed, 40 skipped, 32 deselected (started before S7)
T2 | S5,S7 | settings modal, tests, appearance guide and uniform fields | done | 5e9544c, ef88a0e; 37 tests passed; S7 geometry verified at 80×24 and 180×50

## Log
L1. vamos a hacer algunos cambios: 1- [Image#1] este boton esta genial en funcionalidad pero no muestra a simple vista que hace, 2- [Image#2] este boton esta de mas, 3-esta barra no tiene el mismo look and feel del resto de la aplicacion [Image#3], 4- de el punto 3 hay mucha data repetida esta esa barra justo la barra de abajo con datos como el settings, el play y demas, se repite todo mucho, 5- [Image#4] el cancel es la unica manera de cerrarlo, deberia ser un save and close o solo close o algo por el estilo, revisa este modal en su totalidad, 6- de esa misma imagen ,el compact layout solo cambia el color de la app no hace mas nada
L2. [Image#5] esta muy pegado a nivel de que las opciones de la derecha parecen ser una unica caja, controla eso por favor, misma caja de tamaño para todos
