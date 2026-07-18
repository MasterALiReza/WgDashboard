import sys, os, time
sys.path.append(os.path.join(os.getcwd(), 'src'))
from app import app
from src.modules.WireguardConfiguration import WireguardConfiguration

with app.app_context():
    t0 = time.time()
    c = WireguardConfiguration("wg0")
    t1 = time.time()
    print(f"Time to init config: {t1-t0:.4f}s")

    t0 = time.time()
    c.getPeersTransfer()
    t1 = time.time()
    print(f"Time for getPeersTransfer: {t1-t0:.4f}s")
