# GMGN Multi-Chain V/L Radar

link project: https://github.com/Noya-xen/gmgn-Base-BSC

Port EVM dari GMGN V/L Radar. Radar ini mempertahankan alur sumber: mengambil kandidat dari GMGN Trending, menghitung `V/L`, `FLOW`, dan `S×`, lalu mengirim board `SIGNAL` dan `WATCH` ke Telegram:

- `SIGNAL`: seluruh kandidat dari chain terpilih, maksimal 10 token per chain.
- `WATCH`: kandidat momentum dengan skor minimal, contract address, dan link chart.

Fungsi scoring Watch/LP dari struktur sumber tetap tersedia. `WATCH` sekarang dikirim ke Telegram; `LP` tetap hanya tersedia di kode dan tidak dikirim.

Script ini hanya scanner. Tidak ada private key, signing, swap, atau eksekusi transaksi.

## Perubahan chain

BSC, Base, Arc, dan chain GMGN lain memakai pola command market yang sama seperti board Robinhood pada project sumber. Semua chain bisa dipilih dari konfigurasi; default tetap dua chain untuk mengurangi pemakaian limit API:

- pilihan chain: `sol`, `bsc`, `base`, `eth`, `arbitrum`, `hyperevm`, `robinhood`, `arc`, `stable`
- default: `bsc,base`
- contoh alternatif: `arc,bsc`, `eth,arbitrum`, atau `sol,base,arc`
- jumlah chain: bebas, minimal satu; semua sembilan chain dapat dipilih sekaligus jika limit API mencukupi
- interval: `1h`
- minimum liquidity: `$2,500`
- minimum holders: `200`
- minimum age: `30m`
- minimum smart-degen count: `2`
- minimum swaps: `500`
- minimum market cap: `$100,000`
- tidak memakai `min-gas-fee` karena gate ini tidak dibandingkan lintas chain
- token yang ditandai wash trading tetap dibuang secara lokal

Perhitungan scoring dan format report Telegram tetap sama. Link chart otomatis menggunakan format GMGN `/kline/{chain}/{address}`.

## Requirements

- Python 3.9+
- [GMGN CLI](https://www.npmjs.com/package/gmgn-cli) yang sudah dikonfigurasi
- Telegram bot dan group ID tujuan
- Hermes Agent atau scheduler lain bila ingin menjalankan tiap lima menit

Konfigurasi GMGN CLI:

```bash
npm install -g gmgn-cli
gmgn-cli config
gmgn-cli config --apply YOUR_GMGN_API_KEY
gmgn-cli config --check
gmgn-cli market trending --chain sol --interval 1h --limit 5
gmgn-cli market trending --chain bsc --interval 1h --limit 5
gmgn-cli market trending --chain base --interval 1h --limit 5
gmgn-cli market trending --chain eth --interval 1h --limit 5
gmgn-cli market trending --chain arbitrum --interval 1h --limit 5
gmgn-cli market trending --chain hyperevm --interval 1h --limit 5
gmgn-cli market trending --chain robinhood --interval 1h --limit 5
gmgn-cli market trending --chain arc --interval 1h --limit 5
gmgn-cli market trending --chain stable --interval 1h --limit 5
```

## Setup Telegram

Salin `telegram.env.example` ke:

```text
~/.config/gmgn-bsc-base-radar/telegram.env
```

Isi `TG_BOT_TOKEN`, `TG_RADAR_GROUP_CHAT_ID`, dan `RADAR_CHAINS`. `RADAR_CHAINS` menerima satu atau beberapa pilihan yang dipisahkan koma, misalnya `arc,bsc` atau `sol,base,arc`. `TG_SIGNAL_THREAD_ID` opsional untuk mengirim Signal dan Watch ke topic tertentu. `TG_SEND_WATCH=1` mengaktifkan Watch; ubah menjadi `0` jika hanya ingin Signal. `RADAR_TIMEZONE` memakai nama IANA, misalnya `Asia/Jakarta`.

## Jalankan lokal

```bash
python3 src/gmgn-dlmm-radar.py
```

Untuk menjalankan kombinasi lain sekali saja tanpa mengubah environment:

```bash
python3 src/gmgn-dlmm-radar.py --chains arc,base
```

Jika `TG_RADAR_GROUP_CHAT_ID` kosong, report Signal dicetak ke terminal tanpa mengirim Telegram.

## Install scheduler

Pada Linux/WSL:

```bash
./install.sh
```

Installer menyalin script ke `~/.hermes/scripts/gmgn-dlmm-radar.py`, filter ke `~/gmgn-bsc-base-radar`, dan environment privat ke `~/.config/gmgn-bsc-base-radar/telegram.env`.

Gunakan `config/cron.json` untuk job setiap lima menit.

## File

```text
src/gmgn-dlmm-radar.py     scanner dan Telegram sender
config/filter-query.json   daftar chain dan filter umum
config/arc-filter-query.json
config/bsc-filter-query.json
config/base-filter-query.json
config/sol-filter-query.json
config/eth-filter-query.json
config/arbitrum-filter-query.json
config/hyperevm-filter-query.json
config/robinhood-filter-query.json
config/stable-filter-query.json
config/cron.json           jadwal lima menit
telegram.env.example       template environment
install.sh                 installer lokal
tests/test_radar.py        unit test tanpa network/GMGN
```

## Disclaimer

V/L dan FLOW mengukur aktivitas, bukan keamanan atau profitabilitas. Verifikasi contract address, pool, likuiditas, fee tier, slippage, dan risiko token secara manual sebelum mengambil keputusan.
