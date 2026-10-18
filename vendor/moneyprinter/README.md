# Bundled MoneyPrinterTurbo API engine

This is the minimal API runtime copied from the author's local MoneyPrinterTurbo
v1.2.6 working copy. Upstream: https://github.com/harry0703/MoneyPrinterTurbo

The original MIT license and copyright are retained in LICENSE. SOURCE.json
records the SHA256 of each copied source file, including explicitly recorded
distribution changes. This is a local source snapshot, not a claimed upstream
Git commit or a clean unmodified release archive.

Included: app/ (API, schemas, services, task managers), resource/public/index.html,
and a clean public config.example.toml. The Streamlit web UI is unnecessary because
Shaniu provides the user interface, so webui/, its launchers, and demo/test data are
not included. Installed Python dependencies are declared at the repository root.

Not included: private config.toml, API keys, downloaded footage, storage, model
weights, bundled proprietary fonts, or original music files.

The distributed app/config/config.py accepts SHANIU_MPT_CONFIG and refuses to
delete a directory used as a config path. Shaniu creates the private config under
data/moneyprinter/, retains runtime state there, resolves installed subtitle fonts,
and disables empty random-BGM selection. Other adaptation lives in scripts/ rather
than altering the upstream task pipeline.

Start from the root README. Do not start this vendor application directly as a
publicly accessible service: the Shaniu wrapper supplies compatibility, voice,
state persistence, configuration and local-only startup behavior.
