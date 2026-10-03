/* hqall - frontend for the live situational awareness map.
 *
 * All data comes from the backend /api/* routes; the browser only fetches,
 * filters by the current viewport, and renders. All copy is English.
 * Icons: Material Symbols Rounded (Apache-2.0) loaded as a webfont.
 */
/* global L */

(function () {
  "use strict";

  // ---------------------------------------------------------------- config

  const REFRESH = {
    aircraft: 6000,
    aprs: 20000,
    ais: 120000,
    incidents: 60000,
    traffic: 120000,
    weather: 300000,
    clouds: 300000,
  };

  const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
  const ESRI_ATTR = "Imagery &copy; Esri, Maxar, Earthstar Geographics";

  /* Basemaps. Only key-free providers: OSM's own tiles (the same source the
     ADSB10 firmware uses) plus Esri World Imagery. CARTO's basemaps now ask
     for an API key, so the dark look is done in CSS instead of via a provider. */
  const BASEMAPS = {
    osm: {
      url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      dark: true,
      attribution: OSM_ATTR,
      maxZoom: 19,
    },
    osmhot: {
      url: "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
      dark: true,
      attribution: OSM_ATTR + " / Humanitarian OSM Team",
      maxZoom: 19,
    },
    osmplain: {
      url: "https://tile.openstreetmap.de/{z}/{x}/{y}.png",
      dark: false,
      attribution: OSM_ATTR,
      maxZoom: 19,
    },
    esri: {
      url:
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      dark: false,
      attribution: ESRI_ATTR,
      maxZoom: 19,
    },
  };

  /* Material Symbols ligature names, one set per kind of thing. */
  const GLYPH = {
    border: "public",
    plane: "flight",
    planeGround: "flight_land",
    aprs: "settings_input_antenna",
    vessel: "directions_boat",
    vesselMoored: "anchor",
    incident: "emergency",
    police: "local_police",
    fire: "local_fire_department",
    medical: "medical_services",
    roadworks: "construction",
    accident: "car_crash",
    hazard: "warning",
    flood: "flood",
    storm: "thunderstorm",
    power: "bolt",
    water: "water_drop",
    fuel: "local_gas_station",
    rail: "train",
    event: "celebration",
    closure: "block",
    ice: "ac_unit",
    congestion: "traffic",
    ferry: "directions_boat",
    traffic: "traffic",
    weather: "warning",
  };

  /* Weather glyphs by kind and day/night. Material Symbols has a dedicated
     icon for every WMO class (WX map of the older section is unused). */
  const WX = {
    clear: "sunny", partly: "partly_cloudy_day", cloudy: "cloud",
    overcast: "cloud", fog: "foggy", drizzle: "rainy_light",
    rain: "rainy", showers: "rainy", snow: "weather_snowy",
    sleet: "rainy_snow", thunder: "thunderstorm", default: "cloud",
  };
  function wxGlyph(w) {
    let icon = WX[w.weather_kind] || WX.default;
    if ((w.weather_kind === "clear" || w.weather_kind === "partly") && w.is_day === 0) {
      icon = w.weather_kind === "clear" ? "clear_night" : "partly_cloudy_night";
    }
    return icon;
  }

  const state = {
    map: null,
    baseLayer: null,
    basemaps: {},
    bordersLoaded: false,
    origin: { lat: 62.6, lon: 25.3, label: "Central Finland" },
    follow: false,
    radius: 150,
    maxAlt: 45000,
    activeFeed: "incidents",
    /* weather follows a fixed city, not the map centre */
    wx: { label: "Rovaniemi", lat: 66.5028, lon: 25.7345 },
    /* incidents stay fully lit for 15 min, then fade out over the window */
    incidentWindow: 45,
    incidentHold: 15,
    seenIncidents: {},
    incidentsKnown: false,
    data: {
      aircraft: [],
      aprs: [],
      ais: [],
      incidents: [],
      traffic: [],
      weather: null,
    },
    radar: { frames: [], index: -1, timer: null, layer: null, playing: false },
    /* NASA GIBS cloud fraction: the deck itself, where the radar only shows
       what falls out of it */
    cloudcover: { layers: [], spec: null },
    /* 15 minutes of where things have been, per layer */
    trailsOn: true,
    trails: {},
    trailLayers: {},
    trailStore: { ac: {}, aprs: {}, ais: {} },
    layers: {},
    ac: {},
    acSelected: null,
    /* flight number next to every aircraft, toggleable in the settings panel */
    acLabels: true,
    /* APRS: hide the parked digipeaters and iGates, keep cars and boats that
       are actually going somewhere (a few thousand of the former per radius) */
    aprsMoving: true,
    /* ...and the stations that are actually a car or a walker. The APRS SSID
       suffix is what says it: -9 primary mobile (car), -7 HT / on foot.
       Digipeaters (-1..-4), iGates (-10) and weather (-13) fall out on their
       own, so this is on by default and the symbol-codes filter below is for
       the rare trackers that use /m /p as their icon. */
    aprsMp: true,
    /* instead the noise is the digipeaters, iGates and repeaters themselves: /#,
       /&, /r, /R, /[ — keep them off, keep the rest (aircraft, boats, walkers,
       cars under any symbol) visible. */
    aprsDigi: true,
    /* ...and the stations that only ever beaconed over TCP/IP. Off by default:
       in Finland the cars that actually move are mostly internet-injected (-9,
       OH2LAK-10 style client trackers); the radio-only picture is usually empty.
       The SSID + digi-hide filters already take the clutter out, so this stays
       a toggle for a pure RF view. */
    aprsRf: false,
    aprsCalls: true,
    stores: {},
    trafficVisible: [],
    updateInfo: null, // {tag, url} of a newer GitHub release, or null
  };

  // ------------------------------------------------------------- utilities

  const $ = (id) => document.getElementById(id);


  const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);

  // -------------------------------------------------------------- i18n (FI/EN)
  const T = {
    en: {
      search: "Find a place (e.g. Tampere, Helsinki)",
      weatherCity: "Weather city",
      aircraft: "Aircraft",
      aprsStations: "APRS stations",
      vessels: "Vessels",
      incidents: "Incidents",
      traffic: "Traffic notices",
      settings: "Settings",
      wxFeels: "Feels", wxWind: "Wind", wxGusts: "Gusts", wxClouds: "Clouds",
      wxHumidity: "Humidity", wxPressure: "Pressure", wxPrecip: "Precip",
      wxVisibility: "Visibility",
      tabInc: "Incidents", tabTraffic: "Traffic", tabAircraft: "Aircraft", tabAis: "Vessels",
      layers: "Layers",
      pAircraft: "Aircraft (ADS-B)", pAcLabels: "Show flight numbers on the map",
      pAprs: "APRS stations", pAprsMoving: "Only moving stations",
      pAprsMp: "Cars (-9) & walkers (-7)", pAprsDigi: "Hide digipeaters / iGates", pAprsRf: "Only RF-heard (no TCP/IP-only)",
      pAprsCalls: "Show callsigns on the map",
      pAis: "AIS vessels", pIncidents: "Incidents (Tilannehuone)",
      pTraffic: "Traffic notices", pBorders: "Country borders",
      incHead: "Incidents", showLast: "Show last",
      incHint: "Reports stay at full brightness for 15 min after they arrive, then fade out towards the end of the window. A new report opens its own popup once.",
      wxHead: "Weather", rainClouds: "Rain & clouds (RainViewer)",
      opacity: "Opacity", play: "play",
      autoHint: "Live weather radar precipitation over the whole map. RainViewer stopped serving its separate satellite cloud frames, so for the cloud deck itself use the MODIS layer below (optional, off until ticked).",
      cloudcover: "Cloud cover (MODIS)",
      modisHint: "White is a full cloud deck, dark grey nearly clear, transparent is outside the satellite pass. The pass is a few hours old at best and only has detail up to zoom 6.",
      trackHead: "Tracking", trails: "15-minute trails (aircraft, moving APRS, vessels)",
      follow: "Follow map centre", radius: "Radius", maxAlt: "Max altitude",
      baseHead: "Base map", osmDarkOp: "OpenStreetMap (dark)",
      osmHotOp: "Humanitarian OSM (dark)", osmPlainOp: "OSM DE (light)",
      esriOp: "Esri satellite",
      statusHead: "Status", legendHead: "Legend",
      legAcLow: "Aircraft — low altitude", legAcHigh: "Aircraft — high altitude",
      legAprs: "APRS station", legVessel: "Vessel", legIncident: "Incident",
      legBorder: "Country border",
      dType: "Type", dReg: "Reg", dAlt: "Altitude", dSpeed: "Speed", dHdg: "Heading",
      dVs: "V/S", dIcao: "ICAO", dSquawk: "Squawk",
      feedEmpty: "Nothing to show for this layer.",
      jpPhoto: "JetPhotos photo",
      updateAvail: "New version available",
      bookmark: "Add to Bookmarks",
      bmTitle: "Add HQALL to Bookmarks",
      bmDesc: "Bookmark HQALL for quick access to your live situational awareness map.",
      bmHint: "Press the shortcut keys above in your browser, or drag the link below to your bookmarks toolbar:",
      bmLinkText: "📌 HQALL Situational Map",
      apiHead: "API Keys (Optional)",
      apiHint: "By default, HQALL runs 100% keyless. Optional integrations below can be enabled with free API keys:",
      lblAisKey: "AISHub Username/Key (Vessels)",
      linkAis: "👉 Get free key at AISHub.net",
      lblAprsKey: "aprs.fi API Key (APRS fallback)",
      linkAprs: "👉 Get free key at aprs.fi/page/api",
    },
    fi: {
      search: "Hae paikkaa (esim. Tampere, Helsinki)",
      weatherCity: "Sääpaikka",
      aircraft: "Lentokoneet",
      aprsStations: "APRS-asemat",
      vessels: "Alukset",
      incidents: "Hälytykset",
      traffic: "Liikennetiedotteet",
      settings: "Asetukset",
      bookmark: "Lisää kirjanmerkkeihin",
      bmTitle: "Lisää HQALL kirjanmerkkeihin",
      bmDesc: "Lisää HQALL kirjanmerkkeihin nopeaa käyttöä varten.",
      bmHint: "Paina yllä olevaa pikanäppäintä selaimessasi tai vedä tämä linkki kirjanmerkkipalkkiin:",
      bmLinkText: "📌 HQALL Tilannekartta",
      apiHead: "API-avaimet (Valinnaiset)",
      apiHint: "Oletuksena kartta toimii täysin ilman avaimia. Voit lisätä alla olevat ilmaiset avaimet lisäominaisuuksille:",
      lblAisKey: "AISHub-käyttäjätunnus/avain (Alukset)",
      linkAis: "👉 Hae ilmainen avain: AISHub.net",
      lblAprsKey: "aprs.fi API-avain (APRS-varayhteys)",
      linkAprs: "👉 Hae ilmainen avain: aprs.fi/page/api",
      wxFeels: "Tuntuu", wxWind: "Tuuli", wxGusts: "Puuskat", wxClouds: "Pilvisyys",
      wxHumidity: "Kosteus", wxPressure: "Paine", wxPrecip: "Sade",
      wxVisibility: "Näkyvyys",
      tabInc: "Hälytykset", tabTraffic: "Liikenne", tabAircraft: "Lentokoneet", tabAis: "Alukset",
      layers: "Tasot",
      pAircraft: "Lentokoneet (ADS-B)", pAcLabels: "Näytä lennonumerot kartalla",
      pAprs: "APRS-asemat", pAprsMoving: "Vain liikkuvat asemat",
      pAprsMp: "Autot (-9) ja jalankulkijat (-7)",
      pAprsDigi: "Piilota digitoistimet / iGateit",
      pAprsRf: "Vain RF-kuullut (ei TCP/IP)",
      pAprsCalls: "Näytä kutsumerkit kartalla",
      pAis: "AIS-alukset", pIncidents: "Hälytykset (Tilannehuone)",
      pTraffic: "Liikennetiedotteet", pBorders: "Maiden rajat",
      incHead: "Hälytykset", showLast: "Näytä viimeiset",
      incHint: "Hälytykset pysyvät täydessä kirkkaudessa 15 min saapumisen jälkeen, ja himmenevät ikkunan loppua kohti. Uusi hälytys avaa oman ponnahdusikkunansa kerran.",
      wxHead: "Sää", rainClouds: "Sade & pilvet (RainViewer)",
      opacity: "Läpinäkyvyys", play: "toista",
      autoHint: "Live-sadetutka koko kartalla. RainViewer lopetti erillisten satelliittipilviruutujen jakelun, joten pilvipeitettä varten käytä alla olevaa MODIS-tasoa (valinnainen, pois päältä kunnes valitaan).",
      cloudcover: "Pilvipeite (MODIS)",
      modisHint: "Valkoinen on täysi pilvikansi, tummanharmaa lähes selkeä, läpinäkyvä on satelliitin peiton ulkopuolella. Ohitus on enintään muutaman tunnin vanha ja yksityiskohdat loppuvat zoomin 6 yläpuolella.",
      trackHead: "Seuranta", trails: "15 minuutin jäljet (lentokoneet, liikkuvat APRS, alukset)",
      follow: "Seuraa kartan keskusta", radius: "Säde", maxAlt: "Maksimikorkeus",
      baseHead: "Taustakartta", osmDarkOp: "OpenStreetMap (tumma)",
      osmHotOp: "Humanitarian OSM (tumma)", osmPlainOp: "OSM DE (vaalea)",
      esriOp: "Esri satelliitti",
      statusHead: "Tila", legendHead: "Selite",
      legAcLow: "Lentokone — matala korkeus", legAcHigh: "Lentokone — korkea korkeus",
      legAprs: "APRS-asema", legVessel: "Alus", legIncident: "Hälytys",
      legBorder: "Maan raja",
      dType: "Tyyppi", dReg: "Rekisteri", dAlt: "Korkeus", dSpeed: "Nopeus",
      dHdg: "Suunta", dVs: "Nousunopeus", dIcao: "ICAO", dSquawk: "Squawk",
      feedEmpty: "Ei näytettävää tälle tasolle.",
      jpPhoto: "JetPhotos-kuva",
      updateAvail: "Uusi versio saatavilla",
    },
  };

  /* Lang: "en"/"fi". Remembered, default English. */
  let lang = "en";

  function t(key) {
    return (T[lang] && T[lang][key] != null) ? T[lang][key] : (T.en[key] != null ? T.en[key] : key);
  }

  /* Weather descriptions come from the backend in English; the Finnish UI
     asks Open-Meteo for Finnish_weather_descriptions via a per-kind map. */
  const WX_FI = {
    clear: "Selkeää", partly: "Puolipilvistä", cloudy: "Pilvistä",
    overcast: "Pilvistä", fog: "Sumua", drizzle: "Tihkusadetta",
    rain: "Sateista", showers: "Sadekuuroja", sleet: "Räntää",
    snow: "Lumisadetta", thunder: "Ukkosta",
  };
  function wxDescFi(kind, en) {
    return lang === "fi" && WX_FI[kind] ? WX_FI[kind] : en;
  }

  /* Tilannehuone categories arrive already translated to English by lang.py;
     map the English labels back for the Finnish UI. */
  const INC_FI = {
    "Other incident": "Muu hälytys",
    "Building fire": "Rakennuspalo",
    "Building fire (minor)": "Rakennuspalo (pieni)",
    "Building fire (medium)": "Rakennuspalo (keskikoko)",
    "Building fire (major)": "Rakennuspalo (suuri)",
    "Vehicle fire": "Ajoneuvopalo",
    "Vehicle fire (minor)": "Ajoneuvopalo (pieni)",
    "Vehicle fire (medium)": "Ajoneuvopalo (keskikoko)",
    "Traffic accident": "Liikenneonnettomuus",
    "Road traffic accident": "Tieliikenneonnettomuus",
    "Road accident (minor)": "Tieonnettomuus (pieni)",
    "Road accident (medium)": "Tieonnettomuus (keskikoko)",
    "Road accident (major)": "Tieonnettomuus (suuri)",
    "Fire alarm": "Palohälytys",
    "Oil spill": "Öljyvahinko",
    "Rescue (other)": "Pelastustehtävä (muu)",
    "Rescue": "Pelastustehtävä",
    "Animal rescue": "Eläimen pelastaminen",
    "Accident / hazardous situation at sea or ashore": "Onnettomuus/vaaratilanne merellä tai maissa",
    "Vessel breakdown (not under way)": "Tekninen vika, kohde ei ajelehdi",
    "Technical fault": "Tekninen vika",
    "Other": "Muu",
  };
  function incCatFi(en) { return lang === "fi" && INC_FI[en] ? INC_FI[en] : en; }

  const TRAFFIC_FI = {
    "Accident": "Onnettomuus",
    "Congestion / queue": "Ruuhka / jono",
    "Road works": "Tietöitä",
    "Road closed": "Tie suljettu",
    "Weather related": "Säähän liittyvä",
    "Weight / width limit": "Paino-/leveysrajoitus",
    "Obstacle on road": "Este tiellä",
    "Crane operation": "Nosturin toiminta",
    "Power line work": "Sähköjohtotyö",
    "Technical fault": "Tekninen vika",
    "Traffic notice": "Liikennetiedote",
  };
  function trafficFi(en) { return lang === "fi" && TRAFFIC_FI[en] ? TRAFFIC_FI[en] : en; }

  /* Walk the DOM, re-render the static chrome, then re-run the dynamic bits
     that carry labels (feed rows, weather card, statuses). */
  function applyLang(next) {
    lang = next === "fi" ? "fi" : "en";
    try { localStorage.setItem("hqall.lang", lang); } catch (err) { /* private mode */ }
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      el.setAttribute("placeholder", t(el.getAttribute("data-i18n-ph")));
    });
    document.querySelectorAll("[data-i18n-tip]").forEach(function (el) {
      el.setAttribute("title", t(el.getAttribute("data-i18n-tip")));
    });
    const toggle = $("lang-toggle");
    if (toggle) {
      toggle.textContent = lang === "en" ? "FI" : "EN";
      toggle.title = lang === "en" ? "Suomeksi" : "In English";
    }
    if (state.data.weather) paintWeather(state.data.weather);
    renderFeed();
    renderUpdateBanner();
  }

  const n0 = (v) => (v == null || Number.isNaN(Number(v)) ? "--" : String(Math.round(Number(v))));
  const n1 = (v) => (v == null || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(1));
  const deg = (v) => (v == null ? "--" : n0(v) + "\u00b0");
  const pad2 = (n) => String(n).padStart(2, "0");

  const CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  function compass(degv) {
    if (degv == null) return "";
    return CARDINALS[Math.round(Number(degv) / 45) % 8];
  }
  function hdg(degv) {
    if (degv == null) return "--";
    return n0(degv) + "\u00b0 " + compass(degv);
  }

  function when(iso) {
    if (!iso) return "--";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "--";
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (Math.abs(mins) < 1) return "just now";
    const past = mins > 0;
    const m = Math.abs(mins);
    let txt;
    if (m < 60) txt = m + " min";
    else if (m < 1440) txt = Math.round(m / 60) + " h";
    else txt = Math.round(m / 1440) + " d";
    return past ? txt + " ago" : "in " + txt;
  }

  function stamp(iso) {
    if (!iso) return "--";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).replace("T", " ").slice(0, 16);
    return (
      d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate()) +
      " " + pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()) + " UTC"
    );
  }

  /* Is a point inside the current viewport, with a small margin so items just
     off screen are still drawn? */
  function inView(lat, lon) {
    if (lat == null || lon == null) return false;
    return state.map.getBounds().pad(0.05).contains([lat, lon]);
  }

  function kv(rows) {
    let out = '<div class="popup-grid">';
    rows.forEach(function (r) {
      if (!r) return;
      const k = Array.isArray(r) ? r[0] : r;
      const v = Array.isArray(r) ? r[1] : "";
      if (v === "" || v == null) return;
      out += "<span>" + esc(k) + "</span><span>" + v + "</span>";
    });
    return out + "</div>";
  }

  function link(url, text) {
    if (!url) return "";
    return '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(text) + "</a>";
  }

  // ----------------------------------------------------------------- icons

  /* Chip: coloured disc with an icon glyph. One helper for every non-aircraft
     marker so all layers share the same visual language. */
  function chipIcon(glyph, color, size, weight) {
    const s = size || 20;
    return L.divIcon({
      html:
        '<div class="chip" style="--chip-fg:' + color + ";--chip-bg:" + color +
        "22;width:" + s + "px;height:" + s + 'px">' +
        '<i class="ms" style="--ms-opsz:' + Math.round(s * 0.62) +
        (weight ? ";--ms-wght:" + weight : "") + '">' + glyph + "</i></div>",
      className: "",
      iconSize: [s, s],
      iconAnchor: [s / 2, s / 2],
      popupAnchor: [0, -s / 2],
    });
  }

  function acColor(ac) {
    if (ac.on_ground) return "#9aa7b4";
    const h = Math.min(1, (ac.altitude_ft || 0) / 40000);
    return "hsl(" + Math.round(205 - h * 205) + ",90%," + Math.round(52 + h * 8) + "%)";
  }

  function acSize(ac) {
    if (ac.on_ground) return 20;
    return 22 + Math.min(12, (ac.altitude_ft || 0) / 3000);
  }

  function planeIcon(ac, selected) {
    const color = acColor(ac);
    const size = acSize(ac);
    const glyph = ac.on_ground ? GLYPH.planeGround : GLYPH.plane;
    const rot = ac.track == null ? 0 : ac.track;
    return L.divIcon({
      html:
        '<div class="ac-icon' + (selected ? " sel" : "") + '" style="--ac-color:' + color +
        ";width:" + size + "px;height:" + size + 'px">' +
        '<i class="ms" style="--ms-opsz:' + Math.round(size * 0.9) +
        ";transform:rotate(" + rot + 'deg)">' + glyph + "</i></div>",
      className: "",
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -size / 2],
    });
  }

  /* Map an incident to a glyph. The backend already produced an English
     category string, so match on words. */
  function incidentGlyph(inc) {
    const c = ((inc.category_raw || "") + " " + (inc.category || "") + " " +
      (inc.description_raw || "")).toLowerCase();
    if (/fire|pal|burn|tulva/.test(c)) return GLYPH.fire;
    if (/medical|ambulance|potilaa|ensihoi|sairas/.test(c)) return GLYPH.medical;
    if (/police|poliisi|law enforcement/.test(c)) return GLYPH.police;
    if (/accident|onnettomuus|collision|kolar/.test(c)) return GLYPH.accident;
    if (/road ?work|tie|kadun|työmaa|tyomaa|maintenance/.test(c)) return GLYPH.roadworks;
    if (/flood|water|tulva|kaivo|joki/.test(c)) return GLYPH.flood;
    if (/storm|thunder|myrsky|purra|pete/.test(c)) return GLYPH.storm;
    if (/power|electric|sähk|saha|kaap/.test(c)) return GLYPH.power;
    if (/rail|train|junarata|juna|ratapih/.test(c)) return GLYPH.rail;
    if (/event|tapahtuma|concert|festival/.test(c)) return GLYPH.event;
    if (/fuel|polttoaine|huoltoasema/.test(c)) return GLYPH.fuel;
    if (/ice|jää|liukas/.test(c)) return GLYPH.ice;
    if (/oil|öljy|kaivu/.test(c)) return GLYPH.water;
    if (inc.severity === "accident") return GLYPH.accident;
    if (inc.severity === "major") return GLYPH.hazard;
    return GLYPH.incident;
  }

  const INCIDENT_COLOR = {
    accident: "#ff4d4f",
    major: "#ff7043",
    moderate: "#ffa726",
    minor: "#ffd54f",
    info: "#4dd0e1",
  };

  /* Map a Fintraffic notice to a glyph + colour. */
  const TRAFFIC_STYLE = {
    accident: { glyph: GLYPH.accident, color: "#ff4d4f" },
    roadworks: { glyph: GLYPH.roadworks, color: "#f59f00" },
    congestion: { glyph: GLYPH.congestion, color: "#ffd43b" },
    hazard: { glyph: GLYPH.hazard, color: "#ffa94d" },
    closure: { glyph: GLYPH.closure, color: "#ff8787" },
    ice: { glyph: GLYPH.ice, color: "#74c0fc" },
    weather: { glyph: GLYPH.storm, color: "#a5d8ff" },
    event: { glyph: GLYPH.event, color: "#b197fc" },
    ferry: { glyph: GLYPH.ferry, color: "#ffd43b" },
    traffic: { glyph: GLYPH.traffic, color: "#63e6be" },
  };

  function trafficStyle(kind) {
    return TRAFFIC_STYLE[kind] || TRAFFIC_STYLE.traffic;
  }

  // ------------------------------------------------------- map + basemaps

  function initMap() {
    Object.keys(BASEMAPS).forEach(function (key) {
      const b = BASEMAPS[key];
      state.basemaps[key] = L.tileLayer(b.url, { maxZoom: b.maxZoom, attribution: b.attribution });
    });

    // 19 is the hard ceiling: tile.openstreetmap.org answers "invalid tile"
    // from z20 up, so allowing more only produces a broken map
    state.map = L.map("map", {
      zoomControl: false,
      worldCopyJump: true,
      minZoom: 3,
      maxZoom: 19,
    }).setView([state.origin.lat, state.origin.lon], 6);

    setBasemap($("basemap").value || "osm");
    L.control.zoom({ position: "bottomright" }).addTo(state.map);
    L.control.scale({ imperial: false, position: "bottomright" }).addTo(state.map);
    L.control.attribution({ position: "bottomright", prefix: false }).addTo(state.map);

    state.layers.planes = L.layerGroup().addTo(state.map);
    state.layers.aprs = L.layerGroup().addTo(state.map);
    state.layers.ais = L.layerGroup().addTo(state.map);
    state.layers.incidents = L.layerGroup().addTo(state.map);
    state.layers.trafficLines = L.layerGroup().addTo(state.map);
    state.layers.trafficPins = L.layerGroup().addTo(state.map);
    state.layers.borders = L.layerGroup();

    /* borders sit above the tiles but under the radar and the markers */
    state.map.createPane("borders");
    state.map.getPane("borders").style.zIndex = 250;
    state.map.getPane("borders").style.pointerEvents = "none";

    state.map.on("moveend zoomend", function () {
      renderTraffic();
      if (state.follow) syncOrigin();
    });
  }

  /* Swap the basemap. Dark looking maps are the plain OSM tiles with a CSS
     filter instead of a keyed provider, mirroring what the ADSB10 firmware does
     to the very same tiles. */
  function setBasemap(key) {
    const layer = state.basemaps[key];
    if (!layer) return;
    if (state.baseLayer) state.map.removeLayer(state.baseLayer);
    state.baseLayer = layer.addTo(state.map);
    state.baseLayer.bringToBack();
    state.map.getContainer().classList.toggle("map-dark", !!BASEMAPS[key].dark);
  }

  /* Country / coastline boundaries, drawn from a bundled Natural Earth file so
     the map does not depend on a third party at runtime. */
  async function loadBorders() {
    if (state.bordersLoaded) return;
    state.bordersLoaded = true;
    try {
      const res = await fetch("/static/data/borders.json");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const geo = await res.json();
      L.geoJSON(geo, {
        pane: "borders",
        style: function () {
          return {
            color: "#9ad7ff",
            weight: 1.1,
            opacity: 0.55,
            fill: false,
            interactive: false,
          };
        },
      }).addTo(state.layers.borders);
      setStatus("borders", GLYPH.border, "s-ok", "country borders on");
      if ($("l-borders").checked) toggleLayer("borders", true);
    } catch (err) {
      state.bordersLoaded = false;
      setStatus("borders", GLYPH.border, "s-err", "borders: " + err.message);
    }
  }

  // ------------------------------------------------------------- status UI

  const statusRows = {};

  function setStatus(key, label, cls, text) {
    let row = statusRows[key];
    if (!row) {
      const el = document.createElement("div");
      el.innerHTML = '<span class="ms name"></span><span class="val"></span>';
      $("source-status").appendChild(el);
      row = statusRows[key] = { el: el, name: el.querySelector(".name"), val: el.querySelector(".val") };
    }
    row.el.className = cls;
    if (row.name.textContent !== label) row.name.textContent = label;
    row.val.textContent = text || "";
  }

  function setGlyph(el, name) {
    if (el.textContent !== name) el.textContent = name;
  }

  function setCount(id, value) {
    const b = $(id).querySelector("b");
    if (b.textContent !== String(value)) b.textContent = value;
  }

  // ------------------------------------------------------------- api calls

  async function api(path) {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    const body = await res.json().catch(function () { return null; });
    if (!res.ok) {
      throw new Error(
        (body && (body.error || body.reason)) || res.status + " " + res.statusText
      );
    }
    if (body && body.error) throw new Error(body.error);
    return body;
  }

  /* Query string for the radius-based feeds (routes take lat and lon). */
  function centreQuery() {
    const c = state.map.getCenter();
    return "lat=" + c.lat.toFixed(4) + "&lon=" + c.lng.toFixed(4);
  }

  // -------------------------------------------------------------- aircraft

  function vertRate(v) {
    if (v == null) return "--";
    return (v > 0 ? "+" : "") + Math.round(v * 60) + " ft/min";
  }

  /* The label is the identity. Scheduled flights carry a callsign (FIN515,
     CSC9611) and those are shown; a general-aviation registration (OH-HNX)
     is a surer identity than the pilot's private callsign (CPP), so anything
     without a digit gives way to the registration. */
  function acTitle(ac) {
    const fl = (ac.flight || "").trim();
    if (fl && /\d/.test(fl)) return fl;
    return ac.registration || fl || ac.type || String(ac.hex || "").toUpperCase();
  }

  function acLabel(ac) {
    return acTitle(ac) || "?";
  }

  function acPopup(ac) {
    return (
      '<div class="popup-title"><i class="ms">' + (ac.on_ground ? GLYPH.planeGround : GLYPH.plane) +
      "</i>" + esc(acTitle(ac)) + "</div>" +
      '<div class="popup-sub">' + esc(ac.type || "unknown type") +
      (ac.registration ? " &middot; " + esc(ac.registration) : "") + "</div>" +
      kv([
        ["Altitude", ac.on_ground ? "on ground" : n0(ac.altitude_ft) + " ft"],
        ["Speed", ac.speed_kt == null ? "--" : n0(ac.speed_kt) + " kt"],
        ["Track", hdg(ac.track)],
        ["Vertical rate", vertRate(ac.vertical_rate)],
        ["Squawk", ac.squawk],
        ["ICAO", ac.hex ? String(ac.hex).toUpperCase() : "--"],
        ["Position", ac.lat.toFixed(4) + ", " + ac.lon.toFixed(4)],
      ])
    );
  }

  /* JetPhotos is keyed by registration; Flightradar24 by the ICAO hex. Both
     open in a new tab so the click on the map is not lost. */
  function setAcLinks(ac) {
    const hex = ac.hex ? String(ac.hex).toUpperCase() : "";
    const $fr24 = $("ac-fr24");
    const $jp = $("ac-jp");
    $fr24.href = "https://www.flightradar24.com/" + encodeURIComponent(hex || ac.flight || "plane");
    const reg = (ac.registration || "").trim();
    if (reg) {
      $jp.hidden = false;
      $jp.href = "https://www.jetphotos.com/photo/keyword/" + encodeURIComponent(reg);
    } else {
      $jp.hidden = true;
    }
  }

  /* One label per aircraft, placed to the right of the icon and never
     interactive, so it cannot steal clicks from the marker or the map. */
  function setAcLabel(m, ac) {
    const html = '<span class="ac-label">' + esc(acLabel(ac)) + "</span>";
    if (m.label) {
      m.marker.setTooltipContent(html);
      return;
    }
    m.marker.bindTooltip(html, {
      permanent: true,
      direction: "right",
      offset: [Math.round(acSize(ac) / 2) + 3, 0],
      className: "ac-label-wrap",
      opacity: 1,
      interactive: false,
    });
    m.label = true;
  }

  /* Showing and hiding is left to CSS: unbinding a permanent tooltip leaves
     its element behind in Leaflet 1.9, so toggling the class on the map root
     is both cheaper and the only way that cannot leak. */
  function refreshAcLabels() {
    if (state.acLabels) {
      Object.keys(state.ac).forEach(function (key) {
        const m = state.ac[key];
        if (m.ac) setAcLabel(m, m.ac);
      });
    }
    $("map").classList.toggle("no-ac-labels", !state.acLabels);
  }

  function syncAircraft(list) {
    const layer = state.layers.planes;
    const keep = new Set();

    list.forEach(function (ac) {
      const key = ac.hex || ac.flight;
      if (!key) return;
      keep.add(key);
      const selected = state.acSelected === key;
      const icon = planeIcon(ac, selected);

      let m = state.ac[key];
      if (!m) {
        m = state.ac[key] = {
          marker: L.marker([ac.lat, ac.lon], { icon: icon, keyboard: false, riseOnHover: true }),
        };
        /* Clicking an aircraft fills the top-right detail card; the Leaflet
           popup is deliberately not bound so the map only shows the card. */
        m.marker.on("click", function () { selectAircraft(key, ac); });
        m.marker.addTo(layer);
        if (state.acLabels) setAcLabel(m, ac);
      } else {
        m.marker.setLatLng([ac.lat, ac.lon]);
        m.marker.setIcon(icon);
        if (state.acLabels) setAcLabel(m, ac);
      }
      m.ac = ac;
    });

    Object.keys(state.ac).forEach(function (key) {
      if (keep.has(key)) return;
      state.ac[key].marker.remove();
      delete state.ac[key];
      if (state.acSelected === key) hideAcDetail();
    });

    state.data.aircraft = list;
    renderTrails("ac", list, function (a) { return a.hex || a.flight; }, "#79c9ff");
    setCount("c-aircraft", list.length);
    renderFeed();
  }

  function selectAircraft(key, ac) {
    state.acSelected = key;
    Object.keys(state.ac).forEach(function (k) {
      state.ac[k].marker.setIcon(planeIcon(state.ac[k].ac, k === key));
    });
    fillAcDetail(ac);
  }

  function fillAcDetail(ac) {
    $("ac-detail").hidden = false;
    const acT = acTitle(ac);
    $("ac-title").textContent =
      acT + (ac.registration && !String(acT).includes(ac.registration) ? "  " + ac.registration : "");
    $("ac-type").textContent = ac.type || "-";
    $("ac-reg").textContent = ac.registration || "-";
    $("ac-alt").textContent = ac.on_ground ? "on ground" : n0(ac.altitude_ft) + " ft";
    $("ac-speed").textContent = ac.speed_kt == null ? "-" : n0(ac.speed_kt) + " kt";
    $("ac-hdg").textContent = hdg(ac.track);
    $("ac-vs").textContent = vertRate(ac.vertical_rate);
    $("ac-icao").textContent = ac.hex ? String(ac.hex).toUpperCase() : "-";
    $("ac-squawk").textContent = ac.squawk || "-";
    setAcLinks(ac);
  }

  function hideAcDetail() {
    state.acSelected = null;
    $("ac-detail").hidden = true;
    Object.keys(state.ac).forEach(function (k) {
      state.ac[k].marker.setIcon(planeIcon(state.ac[k].ac, false));
    });
  }

  async function pollAircraft() {
    try {
      const data = await api(
        "/api/aircraft?" + centreQuery() + "&radius=" + state.radius + "&max_alt=" + state.maxAlt
      );
      syncAircraft(data.aircraft || []);
      setStatus("adsb", "flight", "s-ok", (data.count || 0) + " aircraft");
    } catch (err) {
      setStatus("adsb", "flight", "s-err", String(err.message || err));
    }
  }

  // ------------------------------------------------------------ APRS / AIS

  /* Reusable dot-store renderer: keeps one marker per id, updates in place. */
  function syncChips(store, layer, list, idOf, iconOf, popupOf, iconUpdateOf, labelOf) {
    const keep = new Set();
    list.forEach(function (row) {
      const id = idOf(row);
      if (!id) return;
      keep.add(id);
      if (store[id]) {
        store[id].marker.setPopupContent(popupOf(row));
        /* a station that starts (or stops) moving changes colour, but only
           where the caller cares: re-issuing icons every poll would reset the
           aircraft labels and the aircraft detail selection */
        if (iconUpdateOf && store[id].row && store[id].row.moving !== row.moving) {
          store[id].marker.setIcon(iconOf(row));
        }
        if (labelOf) labelOf(store[id], row);
      } else {
        const marker = L.marker([row.lat, row.lon], { icon: iconOf(row), riseOnHover: true });
        marker.bindPopup(popupOf(row));
        marker.addTo(layer);
        store[id] = { marker: marker, row: row };
        if (labelOf) labelOf(store[id], row);
      }
      store[id].row = row;
    });
    Object.keys(store).forEach(function (id) {
      if (keep.has(id)) return;
      store[id].marker.remove();
      delete store[id];
    });
  }

  /* The beacon reports its behaviour through the APRS symbol code that wraps
     the position (e.g. "/p" on foot, "/m" mobile). The code letter decides the
     icon; unknown codes get the generic antenna. */
  const APRS_BEHAVIOUR = {
    p: "directions_walk",      // /p  on foot
    j: "directions_run",       // /j  jogging
    m: "directions_car",       // /m  mobile car
    ">": "directions_car",     // />  car
    "'": "flight",             // /'  aircraft
    a: "flight",               // /a  aircraft
    s: "sailing",              // /s  ship at sea
    y: "sailing",              // /y  yacht
    "8": "sailing",            // digit 8: sailboat
    b: "pedal_bike",           // /b  bicycle
    B: "pedal_bike",
    r: "two_wheeler",          // /r  off-road / rover
    R: "two_wheeler",
    h: "home",                 // /h  home QTH
    H: "home",
    g: "sports_golf",          // /g  golf
    G: "sports_golf",
    u: "bolt",                 // /u  digi? keep the bolt for the notable ones
  };

  /* The callsign SSID suffix is what a station IS — -7 a walker/HT, -9 the
     primary mobile (car), -8 a boat, -14 a trucker, -11 airborne, -13 weather.
     The symbol code is ambiguous (digis and cars share glyphs), so the icon
     follows the SSID FIRST and only falls back to the symbol code for the
     trackers that set one. Sorted ascending, 0..15 covers the APRS spec. */
  const SSID_BEHAVIOUR = {
    "0": "home",
    "1": GLYPH.aprs, // generic additional (digi/mobile/wx)
    "2": GLYPH.aprs, // digi / weather
    "3": GLYPH.aprs, // digi
    "4": GLYPH.aprs, // digi
    "5": GLYPH.aprs, // other networks (D-Star, IRLP, Winlink...)
    "6": GLYPH.aprs, // special activities / satellite / camping
    "7": "directions_walk", // HT, walker, runner
    "8": "sailing", // boat, sailboat, RV, second mobile
    "9": "directions_car", // primary mobile car
    "10": GLYPH.aprs, // iGate / internet
    "11": "flight", // aircraft, balloon, satellite
    "12": GLYPH.aprs, // one-way tracker / device
    "13": "cloud", // weather station
    "14": "local_shipping", // trucker, full-time driver
    "15": "directions_car", // additional mobile
  };

  function aprsSsid(call) {
    const i = (call || "").lastIndexOf("-");
    if (i < 0) return null;
    const s = call.slice(i + 1);
    return /^\d+$/.test(s) ? s.toString() : null;
  }

  function aprsBehaviour(st) {
    const ssid = aprsSsid(st.callsign);
    if (ssid !== null) {
      const fromSsid = SSID_BEHAVIOUR[ssid];
      if (fromSsid) return fromSsid;
    }
    const sym = st.symbol || "";
    const code = sym[sym.length - 1];
    return (code && APRS_BEHAVIOUR[code]) || GLYPH.aprs;
  }

  /* bright green while it is under way, plain while parked, dim while the
     adapter is still waiting for the second fix that settles the question */
  function aprsIcon(r) {
    if (r.moving) return chipIcon(aprsBehaviour(r), r.classified ? "#3ef08a" : "#9fe8bf", 19);
    return chipIcon(aprsBehaviour(r), "#2fbf71", 18);
  }

  /* One callsign label per station, to the right of the icon — same mechanism
     as the aircraft labels, hidden via CSS when the checkbox is off. */
  function setAprsLabel(m, st) {
    const html = '<span class="aprs-label">' + esc(st.callsign || "") + "</span>";
    if (m.label) {
      m.marker.setTooltipContent(html);
      return;
    }
    m.marker.bindTooltip(html, {
      permanent: true,
      direction: "right",
      offset: [12, 0],
      className: "aprs-label-wrap",
      opacity: 1,
      interactive: false,
    });
    m.label = true;
  }

  function refreshAprsLabels() {
    if (state.aprsCalls) {
      Object.keys(state.stores.aprs || {}).forEach(function (key) {
        const m = state.stores.aprs[key];
        if (m && m.row) setAprsLabel(m, m.row);
      });
    }
    $("map").classList.toggle("no-aprs-labels", !state.aprsCalls);
  }

  function aprsPopup(st) {
    return (
      '<div class="popup-title"><i class="ms">' + aprsBehaviour(st) + "</i>" +
      esc(st.callsign || "APRS station") + "</div>" +
      '<div class="popup-sub">' + (st.rf ? "heard on RF" : "internet-only") +
      (st.symbol ? " &middot; symbol " + esc(st.symbol) : "") + "</div>" +
      kv([
        ["Comment", st.comment],
        ["Course", hdg(st.course)],
        ["Speed", st.speed_kt == null ? "--" : n0(st.speed_kt) + " kt"],
        ["Altitude", st.altitude_ft == null ? "--" : n0(st.altitude_ft) + " ft"],
        ["Moving", st.moving
          ? (st.classified === false
            ? "checking, one fix so far"
            : (st.speed_kmh == null
              ? "yes"
              : n0(st.speed_kmh) + " km/h over the last " + n0(Math.round((st.moved_m || 0) / 100) / 10) + " km"))
          : "stationary"],
        ["Distance", st.distance_km + " km " + compass(st.bearing)],
        ["Symbol", st.symbol || "--"],
        ["Full path", st.path_raw || st.path || "--"],
      ])
    );
  }

  async function pollAprs() {
    try {
      const data = await api(
        "/api/aprs?" + centreQuery() +
        "&radius=" + Math.min(250, state.radius) +
        (state.aprsMoving ? "&moving=1" : "&moving=0") +
        (state.aprsRf ? "&rf=1" : "&rf=0") +
        (state.aprsMp ? "&ssid=7,9" : "") +
        (state.aprsDigi ? "&exclude=#,&,r,R,[" : "")
      );
      if (data.ok === false) {
        state.data.aprs = [];
        setCount("c-aprs", 0);
        setStatus("aprs", GLYPH.aprs, "s-wait", data.reason || "unavailable");
        return;
      }
      state.data.aprs = data.stations || [];
      syncChips(
        (state.stores.aprs = state.stores.aprs || {}),
        state.layers.aprs,
        state.data.aprs,
        function (r) { return r.callsign; },
        aprsIcon,
        aprsPopup,
        aprsIcon,
        state.aprsCalls ? setAprsLabel : null
      );
      setCount("c-aprs", state.data.aprs.length);
      /* trails only for the stations that are actually on the move: a parked
         digipeater's "trail" is a dot, and there are hundreds of them */
      renderTrails(
        "aprs",
        state.data.aprs.filter(function (r) { return r.moving; }),
        function (r) { return r.callsign; },
        "#7ff0b0"
      );
      if (data.verified === false) {
        setStatus("aprs", GLYPH.aprs, "s-wait", data.status || "APRS-IS unverified");
      } else if (state.aprsMoving) {
        setStatus("aprs", GLYPH.aprs, "s-ok",
          state.data.aprs.length + " moving of " + (data.tracked || 0));
      } else {
        setStatus("aprs", GLYPH.aprs, "s-ok", state.data.aprs.length + " stations");
      }
      renderFeed();
    } catch (err) {
      setStatus("aprs", GLYPH.aprs, "s-err", String(err.message || err));
    }
  }

  function vesselGlyph(v) {
    const s = (v.nav_status || "").toLowerCase();
    return /anchor|moored|berth/.test(s) ? GLYPH.vesselMoored : GLYPH.vessel;
  }

  function aisPopup(v) {
    return (
      '<div class="popup-title"><i class="ms">' + vesselGlyph(v) + "</i>" +
      esc(v.name || "MMSI " + v.mmsi) + "</div>" +
      '<div class="popup-sub">' + esc(v.nav_status || "Unknown") + "</div>" +
      kv([
        ["MMSI", v.mmsi],
        ["Vessel type", v.type == null ? "--" : v.type],
        ["Speed", v.sog_kn == null ? "--" : n0(v.sog_kn) + " kn"],
        ["Course", hdg(v.cog)],
        ["Heading", v.heading == null ? "--" : deg(v.heading)],
        ["Destination", v.destination],
        ["Distance", v.distance_km + " km"],
        ["Last report", v.timestamp],
      ])
    );
  }

  async function pollAis() {
    try {
      const userAisKey = (localStorage.getItem("hqall.ais_key") || "").trim();
      let query = "/api/ais?" + centreQuery() + "&radius=" + Math.min(250, state.radius);
      if (userAisKey) {
        query += "&key=" + encodeURIComponent(userAisKey);
      }
      const data = await api(query);
      if (data.ok === false) {
        state.data.ais = [];
        setCount("c-ais", 0);
        setStatus("ais", GLYPH.vessel, "s-wait", data.reason || "unavailable");
        return;
      }
      state.data.ais = data.vessels || [];
      syncChips(
        (state.stores.ais = state.stores.ais || {}),
        state.layers.ais,
        state.data.ais,
        function (r) { return r.mmsi; },
        function (r) { return chipIcon(vesselGlyph(r), "#4cc2ff", 19); },
        aisPopup
      );
      setCount("c-ais", state.data.ais.length);
      renderTrails("ais", state.data.ais, function (r) { return r.mmsi; }, "#4cc2ff");
      setStatus("ais", GLYPH.vessel, "s-ok", state.data.ais.length + " vessels");
      renderFeed();
    } catch (err) {
      setStatus("ais", GLYPH.vessel, "s-err", String(err.message || err));
    }
  }

  // ------------------------------------------------------------- incidents

  function incidentPopup(inc) {
    const flags = [];
    if (inc.has_info) flags.push("details");
    if (inc.is_followup) flags.push("follow-up");
    if (inc.has_target) flags.push("responder assigned");
    if (inc.has_photo) flags.push("photos");
    return (
      '<div class="popup-title"><i class="ms">' + incidentGlyph(inc) + "</i>" +
      esc(incCatFi(inc.category) || t("tabInc")) + "</div>" +
      '<div class="popup-sub">' + esc(inc.place || "location not named") + "</div>" +
      (inc.description ? '<div class="popup-text">' + esc(inc.description) + "</div>" : "") +
      kv([
        ["Reported", stamp(inc.time) + " (" + when(inc.time) + ")"],
        ["Severity", inc.severity],
        ["Details", flags.join(", ")],
      ]) +
      '<div class="popup-link">' + link(inc.url, "open report on tilannehuone.fi") + "</div>"
    );
  }

  /* Fresh incidents are fully lit; once they pass the hold window they fade out
     linearly towards the end of the window, so the map always shows what is
     happening right now instead of a pile of yesterday's reports. */
  function incidentOpacity(ageMin) {
    if (ageMin == null) return 0.45;
    if (ageMin <= state.incidentHold) return 1;
    const span = Math.max(1, state.incidentWindow - state.incidentHold);
    const t = (ageMin - state.incidentHold) / span;
    return Math.max(0.08, 0.45 - 0.45 * Math.min(1, t));
  }

  function incidentAge(row) {
    if (row.age_min != null) return row.age_min;
    if (!row.time) return null;
    const d = new Date(row.time);
    if (isNaN(d.getTime())) return null;
    return Math.max(0, (Date.now() - d.getTime()) / 60000);
  }

  function applyIncidentFade() {
    const store = state.stores.incidents;
    if (!store) return;
    Object.keys(store).forEach(function (id) {
      const entry = store[id];
      const el = entry.marker.getElement();
      if (!el) return;
      const op = incidentOpacity(incidentAge(entry.row));
      el.style.opacity = String(op);
      el.style.transition = "opacity 45s linear";
      entry.marker.setZIndexOffset(op < 0.2 ? -400 : 0);
    });
  }

  /* Open the popup of a brand new incident once, so a fresh report is noticed
     without hunting for its chip. The first poll after a page load is treated
     as "already known": arriving on an open map should not be ambushed by a
     popup for something reported an hour ago. */
  function announceNewIncidents(list) {
    let newest = null;
    list.forEach(function (r) {
      const id = r.id || r.lat + "," + r.lon;
      if (state.seenIncidents[id]) return;
      state.seenIncidents[id] = Date.now();
      const age = incidentAge(r);
      if (newest === null || (age != null && age < incidentAge(newest))) newest = r;
    });
    if (!state.incidentsKnown) {
      state.incidentsKnown = true;
      return;
    }
    if (!newest) return;
    const id = newest.id || newest.lat + "," + newest.lon;
    const entry = state.stores.incidents && state.stores.incidents[id];
    if (!entry || !entry.marker.getElement()) return;
    /* pan only when the report is off screen, and never change the zoom */
    if (!state.map.getBounds().pad(0.05).contains([newest.lat, newest.lon])) {
      state.map.panTo([newest.lat, newest.lon], { animate: true });
    }
    entry.marker.openPopup();
    setStatus("incidents", GLYPH.incident, "s-ok", "new: " + (incCatFi(newest.category) || t("tabInc")));
  }

  async function pollIncidents() {
    try {
      const data = await api("/api/incidents?minutes=" + state.incidentWindow);
      const list = data.incidents || [];
      state.data.incidents = list;
      syncChips(
        (state.stores.incidents = state.stores.incidents || {}),
        state.layers.incidents,
        list,
        function (r) { return r.id || r.lat + "," + r.lon; },
        function (r) {
          return chipIcon(incidentGlyph(r), INCIDENT_COLOR[r.severity] || INCIDENT_COLOR.info, 22);
        },
        incidentPopup
      );
      applyIncidentFade();
      setCount("c-incidents", list.length);
      setStatus(
        "incidents", GLYPH.incident, "s-ok",
        list.length + " current (last " + state.incidentWindow + " min)"
      );
      /* last, so a fresh report can override the status line above */
      announceNewIncidents(list);
      renderFeed();
    } catch (err) {
      setStatus("incidents", GLYPH.incident, "s-err", String(err.message || err));
    }
  }

  // --------------------------------------------------------------- traffic

  function trafficPopup(t) {
    const st = trafficStyle(t.kind);
    return (
      '<div class="popup-title"><i class="ms">' + st.glyph + "</i>" + esc(t.title || t.label) + "</div>" +
      '<div class="popup-sub">' + esc(t.place || t.road || "road traffic") + "</div>" +
      (t.description ? '<div class="popup-text">' + esc(t.description) + "</div>" : "") +
      kv([
        ["Type", t.label],
        ["Road", t.road],
        ["Published", stamp(t.time) + " (" + when(t.time) + ")"],
        ["Geometry", t.line ? t.line.length + " points" : "point only"],
      ]) +
      '<div class="popup-link">' + link(t.url, "open original notice") + "</div>"
    );
  }

  /* Traffic is only drawn when it intersects the current viewport: the feed is
     nationwide, the map never is. */
  function renderTraffic() {
    const lines = state.layers.trafficLines;
    const pins = state.layers.trafficPins;
    lines.clearLayers();
    pins.clearLayers();

    const visible = [];
    state.data.traffic.forEach(function (t) {
      let hit = t.point ? inView(t.point[0], t.point[1]) : false;
      if (!hit && t.line) {
        for (let i = 0; i < t.line.length && !hit; i++) {
          hit = inView(t.line[i][0], t.line[i][1]);
        }
      }
      if (hit) visible.push(t);
    });

    visible.forEach(function (t) {
      const st = trafficStyle(t.kind);
      if (t.line && t.line.length > 1) {
        L.polyline(t.line, {
          color: st.color, weight: 6, opacity: 0.35, lineCap: "round",
        }).addTo(lines);
        L.polyline(t.line, {
          color: st.color, weight: 2.5, opacity: 0.95, lineCap: "round",
        }).bindPopup(trafficPopup(t)).addTo(lines);
      }
      const pin = t.point || (t.line && t.line.length ? t.line[Math.floor(t.line.length / 2)] : null);
      if (!pin) return;
      L.marker(pin, { icon: chipIcon(st.glyph, st.color, 22), riseOnHover: true })
        .bindPopup(trafficPopup(t))
        .addTo(pins);
    });

    state.trafficVisible = visible;
    setCount("c-traffic", visible.length);
    $("c-traffic").title = visible.length + " of " + state.data.traffic.length + " notices in view";
    renderFeed();
  }

  async function pollTraffic() {
    try {
      const data = await api("/api/traffic");
      state.data.traffic = data.alerts || [];
      setStatus("traffic", GLYPH.traffic, "s-ok", state.data.traffic.length + " notices");
      renderTraffic();
    } catch (err) {
      setStatus("traffic", GLYPH.traffic, "s-err", String(err.message || err));
    }
  }

  // --------------------------------------------------------- weather panel

  function paintWeather(w) {
    if (!w) return;
    setGlyph($("wx-icon"), wxGlyph(w));
    $("wx-desc").textContent = wxDescFi(w.weather_kind, w.weather || "--");
    $("wx-temp").textContent = n0(w.temperature_c);
    $("wx-feels").textContent =
      n1(w.apparent_temperature_c) + " \u00b0C";
    $("wx-wind").textContent =
      w.wind_kt == null ? "--" : n0(w.wind_kt) + " kn " + compass(w.wind_direction);
    $("wx-gusts").textContent = w.wind_gusts_kt == null ? "--" : n0(w.wind_gusts_kt) + " kn";
    $("wx-clouds").textContent = w.cloud_cover == null ? "--" : n0(w.cloud_cover) + " %";
    $("wx-humidity").textContent = w.humidity == null ? "--" : n0(w.humidity) + " %";
    $("wx-pressure").textContent = w.pressure_hpa == null ? "--" : n0(w.pressure_hpa) + " hPa";
    $("wx-precip").textContent =
      w.precipitation_mm == null
        ? "--"
        : n1(w.precipitation_mm) + " mm" +
          (w.snowfall_cm ? " / " + n1(w.snowfall_cm) + " cm snow" : "");
    $("wx-vis").textContent =
      w.visibility_m == null
        ? "--"
        : w.visibility_m >= 10000 ? "10 km+" : (w.visibility_m / 1000).toFixed(1) + " km";
    $("wx-updated").textContent =
      state.wx.label + " \u00b7 Open-Meteo " + (w.time || "") +
      " \u00b7 elevation " + n0(w.elevation_m) + " m";
  }

  async function pollWeather() {
    const c = state.wx;
    try {
      const w = await api(
        "/api/weather?lat=" + c.lat.toFixed(4) + "&lon=" + c.lon.toFixed(4)
      );
      paintWeather(w);
      setStatus("weather", "thermometer", "s-ok", c.label + " " + n0(w.temperature_c) + " \u00b0C");
      state.data.weather = w;
    } catch (err) {
      setStatus("weather", "thermometer", "s-err", String(err.message || err));
    }
  }

  /* The weather card follows a named city, not the map centre. Default is
     Rovaniemi; any other place is resolved through Open-Meteo's geocoder and
     remembered in localStorage. */
  function loadWxCity() {
    try {
      const raw = localStorage.getItem("hqall.wxCity");
      if (!raw) return;
      const c = JSON.parse(raw);
      if (c && Number.isFinite(c.lat) && Number.isFinite(c.lon)) {
        state.wx = { label: c.label || "Weather", lat: c.lat, lon: c.lon };
      }
    } catch (err) { /* keep the default city */ }
  }

  function setWxCity(label, lat, lon) {
    state.wx = { label: label || "Weather", lat: lat, lon: lon };
    $("wx-city").value = state.wx.label;
    try {
      localStorage.setItem("hqall.wxCity", JSON.stringify(state.wx));
    } catch (err) { /* private mode, city just will not persist */ }
    pollWeather();
  }

  let wxTimer = null;

  async function wxLookup(q) {
    if (q.length < 2) { $("wx-city-results").hidden = true; return; }
    try {
      const res = await fetch(
        "https://geocoding-api.open-meteo.com/v1/search?name=" + encodeURIComponent(q) +
        "&count=6&language=en&format=json"
      );
      const data = await res.json();
      const hits = data.results || [];
      const box = $("wx-city-results");
      box.innerHTML = "";
      if (!hits.length) { box.hidden = true; return; }
      hits.forEach(function (h) {
        const el = document.createElement("div");
        el.innerHTML =
          esc(h.name) + "<small>" +
          esc([h.admin2, h.admin1, h.country].filter(Boolean).join(", ")) + "</small>";
        el.addEventListener("click", function () {
          setWxCity(h.name, h.latitude, h.longitude);
          box.hidden = true;
        });
        box.appendChild(el);
      });
      box.hidden = false;
    } catch (err) {
      $("wx-city-results").hidden = true;
    }
  }

  // ------------------------------------------------------- cloud animation

  function radarOpacity() {
    return Number($("l-clouds-opacity").value) / 100;
  }

  function showFrame(i) {
    const r = state.radar;
    if (!r.frames.length) return;
    r.index = (i + r.frames.length) % r.frames.length;
    const frame = r.frames[r.index];

    if (r.layer) {
      r.layer.setUrl(frame.url);
    } else {
      r.layer = L.tileLayer(frame.url, {
        opacity: radarOpacity(),
        zIndex: 350,
        attribution: "RainViewer",
        // the service has real frames only up to z7 and serves a placeholder
        // above that, so it is declared native and left crisp
        maxNativeZoom: 7,
        maxZoom: 12,
        minZoom: 3,
      }).addTo(state.map);
    }
    r.layer.setOpacity(radarOpacity());
    r.layer.setZIndex(350);

    const f = new Date(frame.time * 1000);
    $("radar-time").textContent =
      pad2(f.getUTCHours()) + ":" + pad2(f.getUTCMinutes()) + " UTC &middot; " +
      frame.kind + (r.index >= r.pastCount ? " (forecast)" : "");
  }

  function stopRadar() {
    state.radar.playing = false;
    if (state.radar.timer) clearInterval(state.radar.timer);
    state.radar.timer = null;
    $("radar-play").innerHTML = "&#9654; play";
  }

  function playRadar() {
    const r = state.radar;
    if (r.playing) { stopRadar(); return; }
    if (!r.frames.length) return;
    r.playing = true;
    $("radar-play").innerHTML = "&#10073;&#10073; pause";
    r.timer = setInterval(function () { showFrame(r.index + 1); }, 550);
  }

  async function loadRadar() {
    try {
      const data = await api("/api/clouds");
      const r = state.radar;
      r.frames = data.frames || [];
      r.pastCount = data.past_count || 0;
      $("radar-play").disabled = r.frames.length < 2;

      const on = $("l-clouds").checked;
      if (!on) {
        if (r.layer) { state.map.removeLayer(r.layer); r.layer = null; }
        r.index = -1;
        $("radar-time").textContent = "--";
        setStatus("clouds", "cloud", "s-wait", "hidden");
        return;
      }

      if (!r.frames.length) {
        setStatus("clouds", "cloud", "s-err", "no frames published");
        return;
      }
      // Start on the newest observed frame, not the forecast one.
      showFrame(r.pastCount ? r.pastCount - 1 : r.frames.length - 1);
      setStatus(
        "clouds", "cloud", "s-ok",
        r.frames.length + " frames (" + r.pastCount + " past / " +
        (r.frames.length - r.pastCount) + " nowcast)"
      );
    } catch (err) {
      setStatus("clouds", "cloud", "s-err", String(err.message || err));
    }
  }

  // ------------------------------------------------------------ 15 min trails

  const TRAIL_MS = 15 * 60 * 1000;
  /* two polylines per entity, the older one fainter, which is a cheap
     stand-in for a gradient that would need a plugin */
  const TRAIL_OLD = { color: "#8ad6ff", weight: 2, opacity: 0.2, interactive: false };
  const TRAIL_NEW = { color: "#8ad6ff", weight: 2, opacity: 0.65, interactive: false };

  function pushTrail(kind, id, lat, lon, at) {
    if (!id || lat == null || lon == null) return null;
    const store = (state.trails[kind] = state.trails[kind] || {});
    let track = store[id];
    if (!track) { track = store[id] = []; }
    const last = track[track.length - 1];
    if (last) {
      if (at <= last[0]) return track;
      /* a station that has not moved does not earn a dot every poll */
      if (Math.abs(lat - last[1]) < 1e-4 && Math.abs(lon - last[2]) < 1e-4) {
        last[0] = at;
        return track;
      }
    }
    track.push([at, lat, lon]);
    const cut = at - TRAIL_MS;
    while (track.length > 2 && track[0][0] < cut) track.shift();
    return track;
  }

  function clearTrail(kind, id) {
    const store = state.trails[kind];
    if (store) delete store[id];
  }

  /* A station's history as the server saw it: the stream has been watching
     the beacon for the last 15 minutes even if the page just opened, and the
     car's trail must be on the map at once — not re-grown from the first poll
     onward. Merges into the live store, later points first. */
  function seedTrail(kind, id, points, now) {
    if (!id || !points || !points.length) return null;
    const store = (state.trails[kind] = state.trails[kind] || {});
    let track = store[id];
    if (!track) { track = store[id] = []; }
    const cut = now - TRAIL_MS;
    points.forEach(function (p) {
      if (p.length < 3 || p[0] < cut) return;
      const last = track[track.length - 1];
      if (last && p[0] <= last[0]) return;
      track.push([p[0], p[1], p[2]]);
    });
    while (track.length > 2 && track[0][0] < cut) track.shift();
    return track;
  }

  function renderTrails(kind, rows, idOf, color) {
    const store = (state.trails[kind] = state.trails[kind] || {});
    const layer = (state.trailLayers[kind] = state.trailLayers[kind] || L.layerGroup().addTo(state.map));
    const keep = new Set();

    if (!state.trailsOn) {
      layer.clearLayers();
      state.trailStore[kind] = {};
      return;
    }

    rows.forEach(function (row) {
      const id = idOf(row);
      if (!id) return;
      keep.add(id);
      const now = Date.now();
      seedTrail(kind, id, row.trail, now);
      const track = pushTrail(kind, id, row.lat, row.lon, now);
      let entry = state.trailStore[kind][id];
      if (!track || track.length < 2) {
        if (entry) { entry.remove(); delete state.trailStore[kind][id]; }
        return;
      }
      const half = Math.floor(track.length / 2);
      const tail = track.slice(0, half).map(function (p) { return [p[1], p[2]]; });
      const head = track.slice(half - 1).map(function (p) { return [p[1], p[2]]; });
      if (!entry) {
        const group = L.layerGroup().addTo(layer);
        entry = state.trailStore[kind][id] = {
          group: group,
          old: L.polyline(tail, TRAIL_OLD).addTo(group),
          new: L.polyline(head, TRAIL_NEW).addTo(group),
        };
      }
      entry.old.setLatLngs(tail);
      entry.new.setLatLngs(head);
      entry.old.setStyle(Object.assign({}, TRAIL_OLD, { color: color }));
      entry.new.setStyle(Object.assign({}, TRAIL_NEW, { color: color }));
    });

    /* a station that is no longer tracked loses its trail with it: no stale
       lines left behind for planes, cars or vessels that left the picture
       (their 15-minute window is enforced by the push, not kept here) */
    Object.keys(state.trailStore[kind]).forEach(function (id) {
      if (keep.has(id)) return;
      state.trailStore[kind][id].remove();
      delete state.trailStore[kind][id];
      delete store[id];
    });
}

  // ------------------------------------------------- cloud deck (NASA GIBS)
  function cloudOpacity() {
    return Number($("l-cloudcover-opacity").value) / 100;
  }

  function showCloudCover(spec) {
    const cc = state.cloudcover;
    cc.spec = spec;
    if (cc.layers.length) {
      cc.layers.forEach(function (l) { state.map.removeLayer(l); });
      cc.layers = [];
    }
    if (!$("l-cloudcover").checked || !spec || !spec.ok || !spec.layers) {
      $("cloudcover-time").textContent = "--";
      setStatus("cloudcover", "cloud", "s-wait", "hidden");
      return;
    }

    spec.layers.forEach(function (entry, i) {
      cc.layers.push(
        L.tileLayer(entry.url, {
          opacity: cloudOpacity(),
          /* under the radar, over the basemap: the cloud sheet is context,
             the rain is what you fly or drive around */
          zIndex: 300 + i,
          attribution: "NASA GIBS",
          // the service only publishes the level 6 tile matrix set, so above
          // zoom 6 the browser scales the tile up; saying so keeps Leaflet
          // from pretending it has detail it does not have
          maxNativeZoom: spec.max_native_zoom || 6,
          maxZoom: 12,
          minZoom: 3,
          // GIBS serves "no store", and a satellite image is not interactive
          interactive: false,
          crossOrigin: false,
        }).addTo(state.map)
      );
    });

    const when = spec.newest_date ? spec.newest_date + " 12:00 UTC" : "--";
    $("cloudcover-time").textContent = when;
    setStatus("cloudcover", "cloud", "s-ok", spec.layers.length + " passes, " + when);
  }

  async function loadCloudCover() {
    try {
      const spec = await api("/api/cloudcover");
      showCloudCover(spec);
    } catch (err) {
      setStatus("cloudcover", "cloud", "s-err", String(err.message || err));
    }
  }

  // -------------------------------------------------------------- feed list

  const SEV_CLASS = {
    accident: "sev-accident", major: "sev-accident",
    moderate: "sev-moderate", minor: "sev-minor", info: "sev-info",
  };

  function feedRows() {
    const tab = state.activeFeed;
    if (tab === "incidents") {
      return state.data.incidents.map(function (r) {
        return {
          cls: SEV_CLASS[r.severity] || "sev-info",
          icon: incidentGlyph(r),
          colour: INCIDENT_COLOR[r.severity] || INCIDENT_COLOR.info,
          title: incCatFi(r.category) || t("tabInc"),
          sub: r.place || "",
          when: when(r.time),
          lat: r.lat, lon: r.lon,
        };
      });
    }
    if (tab === "traffic") {
      return state.trafficVisible.map(function (r) {
        const st = trafficStyle(r.kind);
        return {
          cls: "", icon: st.glyph, colour: st.color,
          title: trafficFi(r.title) || r.label,
          sub: [r.road, r.place].filter(Boolean).join(" \u00b7 "),
          when: when(r.time), lat: r.point[0], lon: r.point[1],
        };
      });
    }
    if (tab === "aircraft") {
      return state.data.aircraft
        .slice()
        .sort(function (a, b) { return (b.altitude_ft || 0) - (a.altitude_ft || 0); })
        .slice(0, 120)
        .map(function (r) {
          return {
            cls: "", icon: r.on_ground ? GLYPH.planeGround : GLYPH.plane,
            colour: acColor(r),
            title: acTitle(r),
            sub: (r.type || "") + " \u00b7 " + (r.altitude_ft || 0) + " ft \u00b7 " +
                 (r.speed_kt == null ? "--" : n0(r.speed_kt) + " kt"),
            when: hdg(r.track), lat: r.lat, lon: r.lon, aircraft: r,
          };
        });
    }
    return state.data.ais.map(function (r) {
      return {
        cls: "", icon: vesselGlyph(r), colour: "#4cc2ff",
        title: r.name || "MMSI " + r.mmsi,
        sub: (r.nav_status || "") + " \u00b7 " + (r.sog_kn == null ? "--" : n0(r.sog_kn) + " kn") +
             " \u00b7 " + hdg(r.cog),
        when: r.timestamp ? String(r.timestamp).slice(11, 16) : "",
        lat: r.lat, lon: r.lon,
      };
    });
  }

  function renderFeed() {
    const tab = state.activeFeed;
    if (document.hidden) return;
    const rows = feedRows();
    const box = $("feed-list");

    if (!rows.length) {
      box.innerHTML = '<div class="empty">' + esc(t("feedEmpty")) + "</div>";
      return;
    }

    let html = "";
    rows.forEach(function (r, i) {
      html +=
        '<div class="item ' + r.cls + '" data-i="' + i + '">' +
        '<div class="line1">' +
        '<i class="ms" style="color:' + r.colour + '">' + r.icon + "</i>" +
        '<span class="cat">' + esc(r.title) + "</span>" +
        '<span class="when">' + esc(r.when) + "</span></div>" +
        '<div class="line2">' + esc(r.sub) + "</div></div>";
    });
    box.innerHTML = html;

    const items = box.querySelectorAll(".item");
    items.forEach(function (el) {
      el.addEventListener("click", function () {
        const row = rows[Number(el.dataset.i)];
        if (!row) return;
        state.map.setView([row.lat, row.lon], Math.max(state.map.getZoom(), 11));
        if (row.aircraft) {
          const key = row.aircraft.hex || row.aircraft.flight;
          if (state.ac[key]) selectAircraft(key, state.ac[key].ac);
        }
      });
    });
  }

  // --------------------------------------------------- origin and geocoding

  function syncOrigin() {
    const c = state.map.getCenter();
    state.origin = { lat: c.lat, lon: c.lng, label: $("origin-label").textContent };
    fetch("/api/origin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: c.lat, lon: c.lng, label: state.origin.label }),
    }).catch(function () { /* origin is a convenience, not critical */ });
  }

  let searchTimer = null;

  async function doSearch(q) {
    if (q.length < 3) { $("search-results").hidden = true; return; }
    try {
      const url =
        "https://geocoding-api.open-meteo.com/v1/search?name=" + encodeURIComponent(q) +
        "&count=6&language=en&format=json";
      const data = await api(url);
      const hits = data.results || [];
      const box = $("search-results");
      box.innerHTML = "";
      if (!hits.length) { box.hidden = true; return; }
      hits.forEach(function (h) {
        const el = document.createElement("div");
        el.innerHTML =
          esc(h.name) + "<small>" +
          esc([h.admin2, h.admin1, h.country].filter(Boolean).join(", ")) + "</small>";
        el.addEventListener("click", function () {
          state.map.setView([h.latitude, h.longitude], 10);
          $("origin-label").textContent = h.name;
          box.hidden = true;
          $("search-input").value = "";
          syncOrigin();
        });
        box.appendChild(el);
      });
      box.hidden = false;
    } catch (err) {
      $("search-results").hidden = true;
    }
  }

  // --------------------------------------------------------------- controls

  function toggleLayer(name, on) {
    const group = state.layers[name];
    if (!group) return;
    if (on) group.addTo(state.map);
    else group.removeFrom(state.map);
  }

  function showPanel(on) {
    $("panel").classList.toggle("hidden", !on);
    $("panel-toggle").setAttribute("aria-expanded", on ? "true" : "false");
    $("panel-toggle").classList.toggle("active", on);
  }

  function wireControls() {
    $("panel-toggle").addEventListener("click", function () {
      showPanel($("panel").classList.contains("hidden"));
    });

    /* language switcher: flip once and let applyLang persist the choice */
    $("lang-toggle").addEventListener("click", function () {
      applyLang(lang === "en" ? "fi" : "en");
    });

    /* the panel is the only settings surface, so a click anywhere else or an
       Escape closes it again */
    document.addEventListener("click", function (ev) {
      if ($("panel").classList.contains("hidden")) return;
      if ($("panel").contains(ev.target) || $("panel-toggle").contains(ev.target)) return;
      showPanel(false);
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      if ($("panel").classList.contains("hidden")) return;
      showPanel(false);
      $("panel-toggle").focus();
    });

    $("feed-collapse").addEventListener("click", function () {
      const feed = $("feed");
      feed.classList.toggle("collapsed");
      $("feed-collapse").innerHTML = feed.classList.contains("collapsed") ? "&#9652;" : "&#9662;";
    });

    document.querySelectorAll(".tab").forEach(function (tab) {
      tab.addEventListener("click", function () {
        document.querySelectorAll(".tab").forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        state.activeFeed = tab.dataset.feed;
        renderFeed();
      });
    });

    $("ac-detail-close").addEventListener("click", hideAcDetail);

    [
      ["l-aircraft", "planes"],
      ["l-aprs", "aprs"],
      ["l-ais", "ais"],
      ["l-incidents", "incidents"],
      ["l-traffic", "trafficLines"],
      ["l-borders", "borders"],
    ].forEach(function (pair) {
      const box = $(pair[0]);
      box.addEventListener("change", function () {
        toggleLayer(pair[1], box.checked);
        if (pair[1] === "trafficLines") {
          toggleLayer("trafficPins", box.checked);
        }
        /* the trails follow their layer on and off the map */
        const tkind = { planes: "ac", aprs: "aprs", ais: "ais" }[pair[1]];
        const tl = tkind && state.trailLayers[tkind];
        if (tl) {
          if (box.checked) tl.addTo(state.map);
          else tl.removeFrom(state.map);
        }
      });
    });

    $("l-ac-labels").checked = state.acLabels;
    refreshAcLabels();
    $("l-ac-labels").addEventListener("change", function () {
      state.acLabels = $("l-ac-labels").checked;
      refreshAcLabels();
    });

    $("l-aprs-moving").checked = state.aprsMoving;
    $("l-aprs-moving").addEventListener("change", function () {
      state.aprsMoving = $("l-aprs-moving").checked;
      pollAprs();
    });

    $("l-aprs-mp").checked = state.aprsMp;
    $("l-aprs-mp").addEventListener("change", function () {
      state.aprsMp = $("l-aprs-mp").checked;
      pollAprs();
    });

    $("l-aprs-digi").checked = state.aprsDigi;
    $("l-aprs-digi").addEventListener("change", function () {
      state.aprsDigi = $("l-aprs-digi").checked;
      pollAprs();
    });

    $("l-aprs-rf").checked = state.aprsRf;
    $("l-aprs-rf").addEventListener("change", function () {
      state.aprsRf = $("l-aprs-rf").checked;
      pollAprs();
    });

    $("l-aprs-calls").checked = state.aprsCalls;
    $("l-aprs-calls").addEventListener("change", function () {
      state.aprsCalls = $("l-aprs-calls").checked;
      refreshAprsLabels();
    });

    $("l-trails").checked = state.trailsOn;
    $("l-trails").addEventListener("change", function () {
      state.trailsOn = $("l-trails").checked;
      /* drop the Leaflet objects but keep the raw 15-minute history, so the
         trails reappear on the very next poll instead of after minutes */
      ["ac", "aprs", "ais"].forEach(function (kind) {
        const store = state.trailStore[kind] || {};
        Object.keys(store).forEach(function (id) { store[id].remove(); });
        state.trailStore[kind] = {};
        const layer = state.trailLayers[kind];
        if (layer) layer.clearLayers();
      });
      pollAircraft();
      pollAprs();
    });

    $("l-cloudcover").addEventListener("change", function () {
      if ($("l-cloudcover").checked) loadCloudCover();
      else {
        const cc = state.cloudcover;
        cc.layers.forEach(function (l) { state.map.removeLayer(l); });
        cc.layers = [];
        cc.spec = null;
        $("cloudcover-time").textContent = "--";
        setStatus("cloudcover", "cloud", "s-wait", "hidden");
      }
    });
    $("l-cloudcover-opacity").addEventListener("input", function () {
      const cc = state.cloudcover;
      cc.layers.forEach(function (l) { l.setOpacity(cloudOpacity()); });
    });

    $("l-clouds").addEventListener("change", function () {
      if ($("l-clouds").checked) loadRadar();
      else {
        stopRadar();
        if (state.radar.layer) { state.map.removeLayer(state.radar.layer); state.radar.layer = null; }
        $("radar-time").textContent = "--";
        setStatus("clouds", "cloud", "s-wait", "hidden");
      }
    });

    $("l-clouds-opacity").addEventListener("input", function () {
      if (state.radar.layer) state.radar.layer.setOpacity(radarOpacity());
    });

    $("radar-play").addEventListener("click", playRadar);

    $("l-radius").addEventListener("input", function () {
      state.radius = Number($("l-radius").value);
      $("radius-label").textContent = state.radius + " NM";
    });
    $("l-radius").addEventListener("change", function () {
      pollAircraft();
      pollAprs();
      pollAis();
    });

    $("l-incident-window").addEventListener("input", function () {
      state.incidentWindow = Number($("l-incident-window").value);
      $("incident-window-label").textContent = state.incidentWindow + " min";
    });

    $("l-incident-window").addEventListener("change", function () {
      /* re-baseline, otherwise every item in the wider window is "new" */
      state.seenIncidents = {};
      pollIncidents();
    });

    $("l-maxalt").addEventListener("change", function () {
      state.maxAlt = Number($("l-maxalt").value) || 45000;
      pollAircraft();
    });

    $("l-follow").addEventListener("change", function () {
      state.follow = $("l-follow").checked;
      if (state.follow) syncOrigin();
    });

    $("basemap").addEventListener("change", function () {
      setBasemap($("basemap").value);
    });

    $("search-input").addEventListener("input", function () {
      clearTimeout(searchTimer);
      const q = $("search-input").value.trim();
      searchTimer = setTimeout(function () { doSearch(q); }, 350);
    });

    $("wx-city").addEventListener("input", function () {
      clearTimeout(wxTimer);
      const q = $("wx-city").value.trim();
      if (q === state.wx.label) { $("wx-city-results").hidden = true; return; }
      wxTimer = setTimeout(function () { wxLookup(q); }, 350);
    });

    $("wx-city").addEventListener("blur", function () {
      setTimeout(function () {
        $("wx-city-results").hidden = true;
        $("wx-city").value = state.wx.label;
      }, 200);
    });

    $("wx-city").addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        $("wx-city-results").hidden = true;
        $("wx-city").value = state.wx.label;
      }
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest(".search")) $("search-results").hidden = true;
      if (!e.target.closest(".wx-head")) $("wx-city-results").hidden = true;
    });

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) renderFeed();
    });
  }

  // ----------------------------------------------------------------- boot

  function checkSymbols() {
    if (!document.fonts || !document.fonts.load) return;
    document.fonts
      .load('24px "Material Symbols Rounded"')
      .then(function (list) {
        if (!list || !list.length) document.body.classList.add("no-symbols");
      })
      .catch(function () { document.body.classList.add("no-symbols"); });
    setTimeout(function () {
      if (!document.fonts.check('24px "Material Symbols Rounded"')) {
        document.body.classList.add("no-symbols");
      }
    }, 4000);
  }

  function schedule(fn, ms) {
    setInterval(fn, ms);
  }

  // ------------------------------------------------------- GitHub update check

  /* The server answers /api/version (installed + latest tag). The banner shows
     once per session per version, is dismissed for a tag in localStorage, and
     reacts to the FI/EN toggle through applyLang like everything else. */
  function renderUpdateBanner() {
    const el = $("update-banner");
    if (!el) return;
    const info = state.updateInfo;
    if (!info) { el.hidden = true; return; }
    let dismissed = null;
    try { dismissed = localStorage.getItem("hqall.dismiss-update"); } catch (err) {}
    if (dismissed === info.tag) { el.hidden = true; return; }
    $("update-link").textContent = t("updateAvail") + " — v" + info.tag;
    $("update-link").href = info.url;
    el.hidden = false;
  }

  function dismissUpdate() {
    if (state.updateInfo) {
      try { localStorage.setItem("hqall.dismiss-update", state.updateInfo.tag); } catch (err) {}
    }
    $("update-banner") && ($("update-banner").hidden = true);
  }

  async function checkUpdate() {
    try {
      const j = await api("/api/version");
      if (j && j.ok) {
        if ($("app-version")) $("app-version").textContent = "v" + String(j.version || "");
        if (j.update_available && j.repo) {
          state.updateInfo = {
            tag: String(j.latest || "").replace(/^v/, ""),
            url: j.release_url || ("https://github.com/" + j.repo),
          };
          renderUpdateBanner();
        }
      }
    } catch (err) { /* offline, or the repo check is off: silently nothing */ }
  }

  async function start() {
    loadWxCity();
    initMap();
    wireControls();
    checkSymbols();
    let saved;
    try { saved = localStorage.getItem("hqall.lang"); } catch (err) { saved = null; }
    const forced = new URLSearchParams(location.search).get("lang");
    applyLang(forced || (saved === "fi" ? "fi" : "en"));
    $("radius-label").textContent = state.radius + " NM";
    $("incident-window-label").textContent = state.incidentWindow + " min";
    $("wx-city").value = state.wx.label;
    loadBorders();

    try {
      const health = await api("/api/health");
      const s = health.sources || {};
      if (/disabled/.test(s.aprs || "")) setStatus("aprs", GLYPH.aprs, "s-wait", "no API key set");
      if (/disabled/.test(s.ais || "")) setStatus("ais", GLYPH.vessel, "s-wait", "no API key set");
    } catch (err) { /* status rows fill in as each poll succeeds */ }

    $("update-dismiss").addEventListener("click", dismissUpdate);
    checkUpdate();

    const isMac = (navigator.platform || "").toUpperCase().indexOf("MAC") >= 0 || (navigator.userAgent || "").toUpperCase().indexOf("MAC") >= 0;
    const keyEl = $("bm-key-mod");
    if (keyEl) keyEl.textContent = isMac ? "Cmd" : "Ctrl";

    const bmModal = $("bookmark-modal");
    const bmBtn = $("bookmark-btn");
    const bmClose = $("bm-close");
    const bmLink = $("bm-link");

    if (bmLink) {
      bmLink.href = window.location.origin + window.location.pathname;
    }
    if (bmBtn && bmModal) {
      bmBtn.addEventListener("click", function () {
        bmModal.removeAttribute("hidden");
      });
    }
    if (bmClose && bmModal) {
      bmClose.addEventListener("click", function () {
        bmModal.setAttribute("hidden", "");
      });
    }
    if (bmModal) {
      bmModal.addEventListener("click", function (e) {
        if (e.target === bmModal) {
          bmModal.setAttribute("hidden", "");
        }
      });
    }
    if (new URLSearchParams(location.search).get("bookmark") === "1" || new URLSearchParams(location.search).get("setup") === "1") {
      if (bmModal) bmModal.removeAttribute("hidden");
    }

    const aisInput = $("cfg-ais-key");
    const aprsInput = $("cfg-aprs-key");
    if (aisInput) {
      try { aisInput.value = localStorage.getItem("hqall.ais_key") || ""; } catch (e) {}
      aisInput.addEventListener("change", function () {
        try { localStorage.setItem("hqall.ais_key", aisInput.value.trim()); } catch (e) {}
        pollAis();
      });
    }
    if (aprsInput) {
      try { aprsInput.value = localStorage.getItem("hqall.aprs_key") || ""; } catch (e) {}
      aprsInput.addEventListener("change", function () {
        try { localStorage.setItem("hqall.aprs_key", aprsInput.value.trim()); } catch (e) {}
        pollAprs();
      });
    }

    pollAircraft();
    pollIncidents();
    pollTraffic();
    pollAprs();
    pollAis();
    pollWeather();
    loadRadar();
    if ($("l-cloudcover").checked) loadCloudCover();

    schedule(pollAircraft, REFRESH.aircraft);
    schedule(pollIncidents, REFRESH.incidents);
    schedule(pollTraffic, REFRESH.traffic);
    schedule(pollAprs, REFRESH.aprs);
    schedule(pollAis, REFRESH.ais);
    schedule(pollWeather, REFRESH.weather);
    schedule(loadRadar, REFRESH.clouds);
    schedule(function () { if ($("l-cloudcover").checked) loadCloudCover(); }, REFRESH.clouds);

    /* keep the incident fade moving even when no new data arrives */
    setInterval(applyIncidentFade, 30000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
