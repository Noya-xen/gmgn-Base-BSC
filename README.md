# GMGN Multi-Chain V/L Radar

link project: https://github.com/Noya-xen/gmgn-Base-BSC

Port EVM dari GMGN V/L Radar. Radar ini mempertahankan alur sumber: mengambil kandidat dari GMGN Trending, menghitung `V/L`, `FLOW`, dan `S×`, lalu mengirim board `SIGNAL`, `WATCH`, dan `VOLUME SPIKE` ke Telegram:

- `SIGNAL`: seluruh kandidat dari chain terpilih, maksimal 10 token per chain.
- `WATCH`: kandidat momentum dengan skor minimal, contract address, dan link chart.
- `VOLUME SPIKE`: kandidat volume besar dari chain terpilih, memakai volume 15m, rolling 1h, dan rasio spike.

Fungsi scoring Watch/LP dari struktur sumber tetap tersedia. `WATCH` sekarang dikirim ke Telegram; `LP` tetap hanya tersedia di kode dan tidak dikirim.

Script ini hanya scanner. Tidak ada private key, signing, swap, atau eksekusi transaksi.

## Perubahan chain

BSC, Base, Arc, dan chain GMGN lain memakai pola command market yang sama seperti board Robinhood pada project sumber. Semua chain bisa dipilih dari konfigurasi; default tetap dua chain untuk mengurangi pemakaian limit API:

- pilihan chain: `sol`, `bsc`, `base`, `eth`, `arbitrum`, `hyperevm`, `robinhood`, `arc`, `stable`
- default RADAR: `bsc,base`
- default VOLUME: mengikuti `RADAR_CHAINS` jika `VOLUME_CHAINS` belum diisi
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

Isi `TG_BOT_TOKEN` dan `TG_RADAR_GROUP_CHAT_ID`. Chain SIGNAL/WATCH dan VOLUME diatur terpisah:

```env
RADAR_CHAINS=bsc,base
VOLUME_CHAINS=arc,eth,arbitrum
```

`RADAR_CHAINS` hanya dipakai SIGNAL/WATCH, sedangkan `VOLUME_CHAINS` hanya dipakai VOLUME SPIKE. `TG_SIGNAL_THREAD_ID` mengatur topic SIGNAL, `TG_SEND_WATCH_THREAD_ID` mengatur topic WATCH, dan `TG_VOLUME_THREAD_ID` mengatur topic VOLUME SPIKE. `TG_SEND_WATCH=1` mengaktifkan Watch; ubah menjadi `0` jika hanya ingin Signal. `RADAR_TIMEZONE` memakai nama IANA, misalnya `Asia/Jakarta`.

Scan tetap dipicu setiap lima menit. Dalam setiap siklus, SIGNAL dan WATCH diproses lebih dulu. Volume memakai sisa waktu sampai jadwal berikutnya; jika belum selesai, cursor disimpan di `~/.config/gmgn-bsc-base-radar/volume-state.json` lalu dilanjutkan pada siklus berikutnya. Alert volume hanya dikirim jika ada kandidat yang memenuhi threshold.

Threshold awal volume:

```text
V15 >= 500k
V1H >= 1M
SPIKE >= 2.0x
```

`SPIKE` dihitung sebagai `V15 / (V1H / 4)`. Threshold tersebut dianggap memakai satuan volume yang dikembalikan GMGN CLI; verifikasi output raw terlebih dahulu sebelum menganggapnya sebagai USD.

## Jalankan lokal

```bash
python3 src/gmgn-dlmm-radar.py
```

Untuk menjalankan kombinasi lain sekali saja tanpa mengubah environment:

```bash
python3 src/gmgn-dlmm-radar.py --chains arc,base
```

Untuk memilih chain volume secara terpisah pada satu kali eksekusi:

```bash
python3 src/gmgn-dlmm-radar.py --chains bsc,base --volume-chains arc,eth
```

Jika `TG_RADAR_GROUP_CHAT_ID` kosong, report Signal dan volume dicetak ke terminal tanpa mengirim Telegram. Jika `TG_VOLUME_THREAD_ID` kosong, alert volume tidak dikirim ke topic general; alert hanya dicetak ke terminal.

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
