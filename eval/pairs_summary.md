# STR-11 per-language-pair smoke (summary)

`python eval/pairs.py` on 2026-10-19, CPU int8, beam 2: the first 50 FLEURS test sentences (text, CC BY 4.0) of each spoken language against the en_us transcription of the same FLoRes id (English -> German against de_de). chrF only (COMET not run). Tier: chrF >= 50 good, >= 40 fair, else experimental; languages served by the many-languages model say so. Text-in only, so speech-recognition errors are not included.

| Lang | Name | n | Translator | chrF own | chrF opus-mul | Tier |
|---|---|---|---|---|---|---|
| de | German | 50 | opus-de | 65.9 | 57.9 | good |
| fr | French | 50 | opus-fr | 68.1 | 58.9 | good |
| es | Spanish | 50 | opus-es | 56.8 | 52.6 | good |
| it | Italian | 50 | opus-it | 61.4 | 54.4 | good |
| nl | Dutch | 50 | opus-nl | 60.5 | 52.4 | good |
| pl | Polish | 50 | opus-pl | 54.5 | 48.2 | good |
| pt | Portuguese | 50 | opus-mul | 63.2 | 63.2 | good (opus-mul) |
| ru | Russian | 50 | opus-ru | 58.2 | 49.7 | good |
| uk | Ukrainian | 50 | opus-uk | 53.9 | 50.2 | good |
| tr | Turkish | 50 | opus-tr | 57.5 | 43.4 | good |
| ar | Arabic | 50 | opus-ar | 59.2 | 45.9 | good |
| fa | Persian | 50 | opus-mul | 26.8 | 26.8 | experimental (opus-mul) |
| ur | Urdu | 50 | opus-ur | 35.2 | 39.8 | experimental |
| hi | Hindi | 50 | opus-hi | 44.5 | 44.0 | fair |
| zh | Chinese | 50 | opus-zh | 53.8 | 43.6 | good |
| ja | Japanese | 50 | opus-ja | 53.0 | 38.4 | good |
| ko | Korean | 50 | opus-ko | 49.2 | 2.1 | fair |
| sv | Swedish | 50 | opus-sv | 68.6 | 58.2 | good |
| da | Danish | 50 | opus-da | 66.8 | 59.3 | good |
| fi | Finnish | 50 | opus-fi | 61.1 | 44.9 | good |
| cs | Czech | 50 | opus-cs | 59.9 | 50.6 | good |
| hu | Hungarian | 50 | opus-hu | 57.1 | 44.7 | good |
| ro | Romanian | 50 | opus-mul | 58.3 | 58.3 | good (opus-mul) |
| el | Greek | 50 | opus-mul | 47.5 | 47.5 | fair (opus-mul) |
| bg | Bulgarian | 50 | opus-bg | 63.3 | 55.3 | good |
| id | Indonesian | 50 | opus-id | 61.5 | 48.2 | good |
| vi | Vietnamese | 50 | opus-vi | 49.4 | 39.0 | fair |
| en | English | 50 | opus-en | 63.1 | - | good |

Notes: Urdu's own Opus-MT scores below opus-mul (35.2 vs 39.8) and Persian is weak (26.8); Korean on opus-mul is unusable (2.1), so the own ko model must stay. No default is changed from this run alone (adoption stays with QA).
