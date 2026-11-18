# Windows performance and player UI redesign

Next stage for the Windows desktop player. Planned only; no new thresholds, design approval, or implementation are implied. Baseline and open defects: [Windows desktop follow-up](../../docs/windows-desktop-next.md).

## Specs

S1. “en rendimiento y la velocidad y la mejora de la ui”

S2. “lo del crear el diseño de ui que la verdad est poco trabajada”

## Tasks

T1 | S1 | measure Windows cold/warm launch, first visible frame and first audible playback; inspect stage timings, idle CPU/memory and resize/toggle responsiveness; publish reproducible baseline | pending

T2 | S1 | prioritize the largest measured bottleneck and optimize it without loosening startup/shutdown or cleanup criteria; compare before/after in the same environment | pending after T1

T3 | S2 | create and compare UI design directions for the full player and mini together, starting from the human screenshots and annotated command strip | pending; design selection is human-owned

T4 | S2 | implement only the selected design; verify space, focus, themes, mouse and keyboard, and narrow/DPI behavior | pending after T3

T5 | S1–S2 | complete the real Windows acceptance matrix, regenerate package evidence and reconcile the unresolved independent verification | pending after affected changes

## Log

L1. User request: “listo deja todo commit y push con las evidencias y lo que se debe ahora tabajr es en rendimiento y la velocidad y la mejora de la ui eso lo dejas comno tarea en el odd y un md con todo lo que falte y lo del crear el diseño de ui que la verdad est poco trabajada”. The user supplied screenshots of the corrected native mini and the full window with its command strip marked. Performance work and UI design are deferred to this next stage.
