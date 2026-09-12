# Dataset card

## Sources

- [Raw accidental collisions](https://zenodo.org/records/21927431): 206 continuous recordings.
- [Raw intentional contacts](https://zenodo.org/records/21941203): 239 continuous recordings.
- [Official processing repository](https://github.com/zhang-zengjie/robot-raw-collision-signals).
- [Quick-start windows](https://zenodo.org/records/6461868), used only to audit published counts.
- [Collection paper](https://doi.org/10.1109/TASE.2020.2997094).

Each raw session contains `JK_MsrExtTrq.mat`: time plus seven external torque channels sampled at
1 kHz, in Nm. `JK_moments.mat` contains manually observed event indices. Other raw measurements are
retained for future connectors but are not required in the first model.

The quick-start product contains 6,960 collision, 7,583 intentional-contact, and 14,098 free-motion
windows. All positive windows place onset at 256 ms and omit source-session IDs. Therefore it is not
used for localization or held-out evaluation. The equality
`14,098 = 6,960 + 7,583 - (206 + 239)` confirms that its free windows are the adjacent-event
midpoints from the same continuous sessions, making random row splits especially leaky.

Dataset files are attributed under CC BY 4.0 based on current Zenodo API metadata; processing code
has its own repository license. Preserve source URLs, checksums, and paper citation when redistributing
a derived dataset.

## Known limitations

- One KUKA LWR4+ robot and end-effector interaction location.
- Seven human experimenters were involved, but subject IDs are not mapped in public file metadata.
- Experiment class is confounded with implement: soft hammer for collision, gloved hand for contact.
- Session/date/trajectory and semantics may be correlated.
- Joint evidence and evidence intervals are derived; only event class and manual onset are source labels.
- Complete-window diagnosis is retrospective and cannot establish real-time detection latency.
