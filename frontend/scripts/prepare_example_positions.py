"""Export local example PosMsr recordings using the reference-validated convention.

No downloads. This checks timestamps, finite angles and joint limits, but does not
claim an independent Jacobian check for each recording (those Jcb files are absent).
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.io import loadmat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-root', type=Path, required=True)
    args = parser.parse_args()
    out = Path(__file__).resolve().parents[1] / 'public/robot/kuka'
    geometry = json.loads((out / 'kinematics.json').read_text())
    reference = json.loads((out / 'position-validation.json').read_text())
    assert reference['mappingVerified'] and reference['recordingId'] == '05-28-21-25'
    limits = np.array([joint['limitsRadians'] for joint in geometry['joints']])
    for folder, rid in [('collision', '03-15-12-53'), ('contact', '03-22-11-18')]:
        source = args.raw_root / folder / rid
        pfile, tfile = source / 'JK_PosMsr.mat', source / 'JK_MsrExtTrq.mat'
        position, torque = loadmat(pfile)['PosMsr'], loadmat(tfile)['MsrExtTrq']
        assert position.shape == torque.shape and position.shape[0] == 8
        assert np.isfinite(position).all() and np.array_equal(position[0], torque[0])
        assert np.allclose(np.diff(position[0]), .001, atol=1e-9, rtol=0)
        indices = np.arange(10, position.shape[1], 10)
        q = position[1:, 10:].T
        assert np.all((q >= limits[:, 0]) & (q <= limits[:, 1]))
        report = {'mappingVerified': False, 'conventionReference': '05-28-21-25',
            'scope': 'Recorded angles using the convention checked on recording 05-28-21-25; no per-recording Jacobian validation. Body and world frame are schematic.',
            'timestampsExactlyMatchTorque': True, 'allAnglesWithinUrdfLimits': True,
            'referenceValidationSha256': hashlib.sha256((out / 'position-validation.json').read_bytes()).hexdigest()}
        payload = {'recordingId': rid, 'endSeconds': float(position[0, -1] + .001), 'angularUnit': 'radian', 'sampleRateHz': 100,
            'times': np.round(position[0, indices], 3).tolist(),
            'joints': [{'channelId': f'joint_{i+1}', 'values': position[i+1, indices].tolist()} for i in range(7)],
            'validation': report, 'provenance': {'sourceUrl': 'https://zenodo.org/records/21927431',
                'sourcePositionSha256': hashlib.sha256(pfile.read_bytes()).hexdigest(),
                'sourceTorqueSha256': hashlib.sha256(tfile.read_bytes()).hexdigest(),
                'sourcePath': f'{folder}/{rid}/JK_PosMsr.mat', 'method': 'Every tenth original sample starting at 0.010 s; no interpolation or rounding of angles.'}}
        (out / f'positions-{rid}.json').write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False)+'\n')
        print(rid, len(indices), 'position samples; reference convention, per-record validation not claimed')

if __name__ == '__main__':
    main()
