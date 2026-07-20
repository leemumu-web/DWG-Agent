# Backend business modules

Each directory owns a business capability, its public interface and its persistence rules. Modules may depend on `app.platform` seams; platform code must never import a business module. During the incremental migration, code not yet grouped remains in the legacy `models`, `schemas`, `services`, `api` and `workers` packages and is moved only with its focused regression suite.
