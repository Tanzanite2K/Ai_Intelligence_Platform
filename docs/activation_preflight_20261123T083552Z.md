# Activation preflight 20261123T083552Z

Production: `C:\Users\User\OneDrive\Documents\Desktop\Infotact\ai-intelligence-platform\data\platform.db` — writes: none (read-only)

- **no_writer**: PASS — `{"side_files": [], "ports_open": {"8000": false, "3000": false}, "mtime_stable_5s": true}`
- **backups**: PASS — `{"dir": "C:\\FrontierAtlas-backups\\activation-20261123T083552Z", "outside_onedrive": true, "cold": {"path": "C:\\FrontierAtlas-backups\\activation-20261123T083552Z\\platform.pre-activation.cold.db", "sha256": "c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff", "sha_matches_productio`
- **production_integrity**: PASS — `{}`
- **selected_sources_usable**: PASS — `{"blocked": []}`
- **production_unchanged**: PASS — `{"sha256_after": "c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff"}`

SHA-256 `c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff`, 41,025,536 bytes, Alembic `0002_history_tables`, integrity ok, FK violations 0

| table | rows |
|---|---|
| alembic_version | 1 |
| canonical_entities | 62 |
| catalogue_scans | 0 |
| claims | 0 |
| crawl_checkpoints | 18 |
| dead_letters | 16 |
| entities | 0 |
| entity_mappings | 4,944 |
| entity_resolution_events | 0 |
| evidence_links | 0 |
| ingestion_batches | 1 |
| metric_snapshots | 0 |
| presence_events | 0 |
| record_observations | 6,708 |
| record_versions | 6,708 |
| records | 6,708 |
| seen_items | 0 |
| source_groups | 0 |
| source_reliability_daily | 0 |
| source_runs | 42 |

Config: `{'history_enabled': True, 'api_read_only': True}`

READY: **True**
