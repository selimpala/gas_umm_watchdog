import subprocess
import sys
import time

def main():
    print("🚀 Başlatılıyor: GIE ve IPEX Watchdog...")
    
    p1 = subprocess.Popen([sys.executable, "gie_rss.py"])
    p2 = subprocess.Popen([sys.executable, "ipex_rss.py"])

    try:
        while True:
            if p1.poll() is not None:
                print("❌ gie_rss.py durdu. Sistem yeniden başlatılıyor...")
                p2.terminate()
                sys.exit(1)
                
            if p2.poll() is not None:
                print("❌ ipex_rss.py durdu. Sistem yeniden başlatılıyor...")
                p1.terminate()
                sys.exit(1)
                
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n🛑 Kullanıcı tarafından durduruldu.")
        p1.terminate()
        p2.terminate()

if __name__ == '__main__':
    main()
