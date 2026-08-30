"""直接解析 CST farfield monitor .ffm 二进制文件，提取 theta/phi/gain 数据。"""
import os
from pathlib import Path

import numpy as np


def read_ffm(path: str) -> dict:
    with open(path, 'rb') as f:
        data = f.read()

    # Find header end
    header_end = data.find(b'\x00', 0)
    header = data[:header_end].decode('ascii', errors='replace')

    # After header: complex64 pairs (Er_theta, Ei_theta, Er_phi, Ei_phi)
    # 181 theta points (0..180) x 361 phi points (0..360)
    n_theta = 181
    n_phi = 361
    n_points = n_theta * n_phi

    # Try to find float data after header
    # CST .ffm: header bytes then raw float32 complex data
    offset = header_end + 1
    # align to 4 bytes
    while offset % 4 != 0:
        offset += 1

    float_data = np.frombuffer(data[offset:], dtype=np.float32)
    # Each point: real_theta, imag_theta, real_phi, imag_phi = 4 floats
    if len(float_data) >= n_points * 4:
        e_theta = float_data[0::4] + 1j * float_data[1::4]
        e_phi = float_data[2::4] + 1j * float_data[3::4]
        e_theta = e_theta[:n_points]
        e_phi = e_phi[:n_points]

        gain_abs = np.abs(e_theta)**2 + np.abs(e_phi)**2

        thetas = np.repeat(np.linspace(0, 180, n_theta), n_phi)
        phis = np.tile(np.linspace(0, 360, n_phi), n_theta)

        return {
            'success': True,
            'n_points': int(n_points),
            'theta': thetas.tolist(),
            'phi': phis.tolist(),
            'gain_linear': gain_abs.tolist(),
            'header': header[:80],
        }
    return {'success': False, 'message': f'Not enough float data: {len(float_data)} < {n_points*4}'}


if __name__ == '__main__':
    import glob

    runtime_root = Path(
        os.environ.get(
            'CST_RUNTIME_ROOT',
            r'D:\cst_agent_rag_data\runtime\cst_agent_workbench',
        )
    )
    pattern = str(runtime_root / 'projects' / 'fast_path' / '**' / 'Result' / '*.ffm')
    files = glob.glob(pattern, recursive=True)
    print('ffm files:', files)
    if files:
        result = read_ffm(files[0])
        print('success:', result.get('success'))
        print('n_points:', result.get('n_points'))
        print('header:', result.get('header', '')[:60])
        if result.get('gain_linear'):
            g = result['gain_linear']
            print('gain range:', min(g), 'to', max(g))
            print('first 5 thetas:', result['theta'][:5])
            print('first 5 phis:', result['phi'][:5])
