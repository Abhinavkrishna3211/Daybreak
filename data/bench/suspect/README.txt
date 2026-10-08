NOT T1 DATA, but not a fluke either. Kept because the anomaly is itself a result.

Asking llama-bench for the whole sweep in one invocation
(-t 1,2,3,4 -p 64,160,320 -n 32,96 -fa 0,1 -r 3) depresses the 4-thread numbers and
leaves every other thread count alone. Two independent runs of that exact call:

  threads   small call (pp/tg)   run #1 (40config)   run #2 (40config_run2)
  1         15.78 / 5.41         15.71 / 5.34        15.78 / 5.42
  2         31.16 / 10.22        31.15 / 10.22       31.15 / 10.27
  3         46.24 / 14.60        45.63 / 14.08       46.16 / 14.11
  4         61.18 / 18.93        49.90 / 13.92       53.99 / 14.87

Only the 4-thread row moves, by 12-18% on prompt and 21-27% on generation, and its
run-to-run stddev inflates to +-0.30/+-0.39 against +-0.02-0.05 elsewhere. Run #1 also
ranked 3 threads above 4 on generation; run #2 did not, so that inversion was a
marginal artefact of the depression and not a separate finding.

llm_speed_40config.csv       run #1, 41 rows (40 configs for A1 plus the one B1 row
                             from the 1b run that was stopped once the method was in
                             doubt)
llm_speed_40config_run2.csv  run #2, the same call repeated from scratch on an idle
                             board
thermal_cpu_sweep_A1.csv     thermal log of run #1, and the evidence that the cause was
                             not the board: all four cores read 2016000 kHz in every one
                             of its 188 samples, peak 68.4 C against a 90 C trip point,
                             3077-3167 MB available throughout
thermal_cpu_sweep_B1.csv     thermal log of the stopped 1b run

Full write-up, including what this rules out and why the shipped figure comes from T2
instead: docs/measurements.md, "A 40-config llama-bench call measures low".
