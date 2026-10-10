# 02Route 3D — Yol Haritası (v1.0)

Hedef: 02Route 3D'yi profil duyarlı 3D rotalamada QGIS'in en güvenilir ve en
hızlı aracı yapmak. Bu plan v0.4.1 kodunun incelenmesine dayanır (rotalama
çekirdeği, QGIS arayüzü ve Processing algoritmaları, 3D web görüntüleyici);
her madde dosya ve satır kanıtıyla tespit edildi. Sıra: önce doğruluk, sonra
donmayan arayüz, sonra hız, sonra görsel kalite ve yeni analizler.

---

## Faz 1 — Doğruluk (rotalar doğru olmalı)

> **Durum (Ekim 2026): tamamlandı (v0.5.0).** 1–12 numaralı maddelerin hepsi
> düzeltildi; her biri için saf Python birim testi eklendi (68 → 83 test,
> CI'da koşuyor). Notlar: tekerlekli sandalye için kesin sınır, konfor
> sınırı %5'in üstünde ADA/ISO 21542 rampa azamisi %8,33; bebek arabası
> %10. Çok kısa DEM segmentlerinde eğim en az 10 m üzerinden ölçülüyor.
> Harita eşlemede ağ mesafesi sınırlı Dijkstra ile; alternatif rota ×4
> ceza yöntemiyle.

1. **Tek yönlü yollar herkese uygulanıyor** — `core/routing_engine.py:283`
   tek yönlü segmentin ters kenarını hiç eklemiyor ve grafik tüm profillerce
   paylaşılıyor: yayalar tek yönlü sokakta ters yönde yürüyemiyor, bisiklet
   `oneway:bicycle=no`'yu yok sayıyor. Düzeltme: ters kenar her zaman eklenir,
   `oneway` bayrağıyla; profil kategorisine göre `evaluate_edge_access` karar
   verir (araç: uyar; yaya: yok say; bisiklet: `oneway:bicycle` etiketine göre).
2. **Tekerlekli sandalye / bebek arabası eğim sınırı uygulanmıyor** —
   `core/mobility_profiles.py:97-100` yaya kategorisinde sınırı aşan eğime
   yalnızca ceza veriyor; %5 sınırlı tekerlekli sandalye %15 rampadan
   geçebiliyor. Düzeltme: erişilebilirlik profillerinde sert sınır (geçilemez);
   yokuş yukarı / aşağı ayrımı.
3. **Pareto sezgiseli kabul edilemez (inadmissible)** — `core/pareto_router.py:186`
   en yüksek hızı `taban×1.5` sayıyor; bisiklet yokuş aşağı 45 km/sa, araç
   80 km/sa gidebiliyor. En hızlı rota yanlış çıkabilir. Düzeltme: profilin
   kinematik modelinden gerçek üst hız.
4. **Epsilon baskınlık testi ters** — `pareto_router.py:40-43`
   `(1+ε)·a ≤ b` yerine standart `a ≤ (1+ε)·b`. "Hypervolume" adlı ölçü
   gerçek hacim değil; adı düzeltilecek ya da gerçek hesap yapılacak.
5. **Deniz seviyesi "veri yok" sayılıyor** — `routing_engine.py:186`
   `z == 0` olan gerçek kot DEM ile eziliyor; DEM yoksa 0'a düşüp komşularla
   uçurum eğimleri yaratıyor. Düzeltme: "yok" için `None`, eksik kotta
   komşulardan enterpolasyon.
6. **Kısa segmentlerde aşırı eğim** — eğim ham düğüm kotundan, 0,1 m'lik
   segmentlerde hesaplanıyor (`routing_engine.py:248`); DEM gürültüsü araçları
   sonsuz maliyete, sandalyeyi bloğa itiyor. `smooth_elevation_series`
   (`profile_stats.py:184`) yalnız istatistikte kullanılıyor. Düzeltme: yol
   boyunca yumuşatma + asgari yatay mesafe.
7. **Araç maliyeti seyahat süresi değil** — uzunluk × hiyerarşi ağırlığı;
   `maxspeed` saklanıyor ama kullanılmıyor, `lanes` geçirilmiyor.
8. **Harita eşleme hataları** — `map_matching_3d.py:127-141` derece üzerinde
   cos(enlem) düzeltmesiz izdüşüm; `cands[:10]` en yakın 10 değil ilk 10;
   geçiş olasılığı ağ mesafesi yerine kuş uçuşu.
9. **Alternatif rota** birincil rotanın tüm kenarlarını yasaklıyor (köprü
   tek ise başarısız); ceza tabanlı yönteme geçilecek.
10. **QGIS 4 / Qt6**: `main_plugin.py:10` `QAction`'ı `QtWidgets`'tan alıyor
    (Qt6'da `QtGui`); `metadata.txt` QGIS 4'ü desteklediğini söylüyor.
11. **Küçükler**: güneş analizi alan takma adları yanlış alanlara
    (`alg_solar_exposure.py:120`); görüntüleyicide "Green/Shade" düğmesi
    yaşlı profilini gösteriyor (`web/js/app3d.js:602`); yerel sunucu
    `Access-Control-Allow-Origin: *` ile rotayı her web sayfasına açıyor
    (`core/local_server.py:22`).
12. **Hatalar yutulmasın** — Overpass zaman aşımı / 429 "bu alanda OSM
    verisi yok" olarak görünüyor (`core/osm_downloader.py:120`,
    `core/network_source.py:171`); DEM parça hataları sessiz
    (`core/dem_fetcher.py:131`). Gerçek neden kullanıcıya gösterilecek.

Her düzeltme saf Python birim testiyle gelir (QGIS gerekmez; CI'da koşar).

## Faz 2 — Donmayan arayüz (QgsTask)

> **Durum (Ekim 2026): 1–5 tamamlandı (v0.6.0); 6 kısmen.** Rotalama, OD
> matrisi, OSM ve DEM indirmeleri QGIS görev yöneticisinde (ilerleme + iptal);
> DEM 25.000 noktalık bütçeyle planlanıyor; Overpass parça parça okunduğu
> için Processing iptali indirmeyi de durduruyor; ayarlar `QgsSettings`'te,
> katman seçimleri projede; senaryolar `*.route3d.json` olarak kaydedilip
> yükleniyor ve önceki çalıştırmayla karşılaştırılıyor. Görev katmanı
> (`gui/tasks.py`) ve ayar/senaryo durumu (`gui/dock_state.py`) ayrıldı;
> sekmelerin ayrı dosyalara bölünmesi Faz 3 ile birlikte sürecek. CI'da
> QGIS 3.34 işi duman ve algoritma testlerini koşuyor (Faz 6.1'in ilk adımı).

1. OSM indirme, DEM çekme ve rotalama `QgsTask`'a taşınır; ilerleme çubuğu,
   iptal, bitince sonuç. Şu an hepsi GUI iş parçacığında: "Tüm harita
   kapsamı DEM" şehir ölçeğinde ~4.000 sıralı istek atıp QGIS'i dakikalarca
   donduruyor (`gui/dock.py:1857`, `core/dem_fetcher.py:88-131`).
2. DEM nokta bütçesi: büyük kapsamda uyarı + üst sınır + gerçek çözünürlüğün
   gösterilmesi; QGIS'te yüklü bir DEM katmanı varsa önce o kullanılır.
3. Processing algoritmalarında `feedback.isCanceled()` ve ilerleme; Overpass
   indirmesine `feedback` geçirilir.
4. Ayarların kalıcılığı (`QSettings`): profil, ağırlıklar, katman seçimleri.
5. Senaryo kaydet/yükle (JSON): başlangıç–bitiş, profiller, ağırlıklar;
   önceki çalıştırmalarla karşılaştırma tablosu.
6. `gui/dock.py` (2.600 satır) sekme başına widget + denetleyici + görev
   katmanı olarak bölünür.

## Faz 3 — Hız

1. Grafik bir kez kurulur, önbelleğe alınır (segment özeti + DEM kimliği);
   şu an her çalıştırma ve her algoritma grafiği baştan kuruyor.
2. Kenar başına raster değerleri ve profil maliyetleri önceden hesaplanır
   (CSR dizileri); A* her gevşetmede üç raster okuması yapıyor
   (`routing_engine.py:500-502`).
3. Yakalama (snapping) uzamsal indeksle ve kenara izdüşümle: bugün bileşen
   başına tam tarama, O(C·N) (`routing_engine.py:356`).
4. OD matrisi N×M ayrı A* yerine N kez bire-çok Dijkstra; izokron aynı
   motorla.
5. Daha güçlü sezgisel: ALT (landmark) veya çift yönlü A*; bugünkü
   0,3–0,6 tabanlı sezgisel A*'ı neredeyse Dijkstra yapıyor.
6. Harita eşlemede uzamsal indeks (bugün her GPS noktası tüm kenarları
   tarıyor).
7. Pareto: etiketlerde tüm yol yerine ebeveyn işaretçisi (O(L²) bellek
   gidiyor), etiket eşleştirme kimlikle.
8. Ölçüm: deterministik test ağları (1k / 10k / 100k kenar) ve CI'da
   benchmark kapısı.

## Faz 4 — 3D görüntüleyici

1. **Gerçek arazi**: bugünkü arazi rota boyunca 180 noktadan uydurulan bir
   yüzey (`web/js/app3d.js:692-916`); rota dışındaki rölyef gerçek değil.
   DEM ızgarasından (QGIS katmanı veya çekilen DEM) gerçek yükseklik ağı;
   ağaçlar ve binalar araziye oturur.
2. Ağaçlar instanced, ortak malzeme (bugün her ağaç yeni malzeme, binlerce
   çizim çağrısı); binalar birleştirilir.
3. İsteğe bağlı çizim (bugün her kare, gölge haritası dahil).
4. Altlık: Web Mercator'a doğru yerleşim, karo başına değil toplu doku
   güncellemesi, çevrimdışı yedek.
5. Gerçek yükseklik profili grafiği: mesafe/kot eksenleri, eğim renkleri,
   grafikte gezinince 3D'de işaret (çift yönlü).
6. Rota karşılaştırma: profiller kalın şerit olarak, yan yana istatistik.
7. Veri aktarımı: 1,5 sn'de bir JSON yoklaması yerine olay (SSE) veya QGIS
   içi gömülü görünüm; hata olursa görünür mesaj.
8. Erişilebilirlik: klavye kontrolü, `:focus-visible`,
   `prefers-reduced-motion`, ekran okuyucu etiketleri.
9. Yüksek çözünürlüklü ekran görüntüsü ve HUD'lu video; Safari için codec
   denetimi.

## Faz 5 — Analiz derinliği

1. Dönüş maliyetleri (kenar tabanlı grafik): kamyon, bisiklet, sandalye.
2. Gece / sıcak saat maliyeti (`lit`, `sidewalk` zaten okunuyor).
3. Gerçek gölge: güneş analizi bugün sokak yönünden sezgisel
   (`core/solar_shadow.py`); bina katmanı varsa gerçek gölge hesabı.
4. İzokronlar dışbükey zarf yerine yol tamponu / konkav zarf (bugünkü alan
   abartılı).
5. Toplu taşıma (GTFS + RAPTOR) ile yürüme bağlantılı çok modlu rota.
6. Gerçek çok amaçlı arama (vektör sezgiselli NAMOA*), arama kesilirse
   kullanıcıya bildirim.

## Faz 6 — Mühendislik ve dürüstlük

1. CI'da QGIS (Docker `qgis/qgis`) işi: duman testi ve algoritma testleri
   (bugün hiç koşmuyor); QGIS 3.x ve 4.x matrisi.
2. Ağ hata yolları, iptal ve CRS gidiş-dönüş testleri.
3. İddiaların düzeltilmesi: "dinamik gölge", "NAMOA* 4D Pareto",
   "Copernicus" adları, "30 m DEM" gerçek davranışla uyumlu hale gelir
   (ya özellik gerçekleşir ya metin düzelir).
4. Web görüntüleyicide modül yapısı ve JS testleri (lint, tip kontrolü,
   görsel regresyon — PlanX 3D City'deki düzen).

---

## Başarı ölçütleri (v1.0)

- Tüm profil kısıtları her araçta sert ve tutarlı; yaya tek yönlü sokakta
  yürür, sandalye sınır üstü rampadan geçmez.
- QGIS hiçbir işlemde donmaz; her uzun iş iptal edilebilir.
- 10k kenarlı ağda tek rota < 0,5 sn, 50×50 OD matrisi < 10 sn.
- 3D görüntüleyicide gerçek DEM arazisi, boşta GPU ≈ %0.
- CI'da QGIS 3 ve 4 üzerinde gerçek algoritma testleri.
