// Locally bundled polygons from the published Astana district layer.
const colors = {esil:'#2563eb',almaty:'#d97706',saryarka:'#7c3aed',baikonyr:'#db2777',nura:'#07856f',saraishyk:'#687586'};
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = value => Number(value).toLocaleString('ru-RU',{maximumFractionDigits:2});
let layer, loading, latest, currentMarkers = [], show = true;
export const boundaryCaption = 'Границы — опубликованный слой геосервиса Астаны. Цветные районы связаны с учебной моделью; серый Сарайшык — без показателей.';
function style(feature) {
  return {color:colors[feature.properties.id] || '#687586',weight:2.7,opacity:.95,
    fillColor:colors[feature.properties.id] || '#687586',fillOpacity:feature.properties.in_dataset ? .12 : .06,
    dashArray:feature.properties.in_dataset?undefined:'6 5'};
}
function popup(feature) {
  const p=feature.properties, r=latest.changes.find(row=>row.id===p.id);
  if (!r) return `<strong>${escape(p.name)}</strong><p>Показатели для района пока не предоставлены.</p>`;
  const district=latest.prediction.districts.find(d=>d.id===p.id);
  const critical=Object.values(district.indicators).filter(v=>v<40).length;
  return `<div class="district-popup"><strong>${escape(r.name)}</strong><p>Факт: <b>${number(r.before)}</b><br>Прогноз: <b>${number(r.after)}</b><br>Изменение: <b>${r.delta>=0?'+':''}${number(r.delta)}</b><br>Критических показателей: <b>${critical}</b></p><small>Показатели синтетической модели</small></div>`;
}
function controls(map) {
  const control=L.control({position:'topright'});
  control.onAdd=()=>{
    const box=L.DomUtil.create('div','district-map-controls');
    const toggle=document.createElement('button');toggle.type='button';toggle.textContent='Границы';toggle.setAttribute('aria-pressed','true');
    toggle.onclick=()=>{show=!show;toggle.setAttribute('aria-pressed',String(show));if(show)layer.addTo(map);else layer.remove();};
    const fit=document.createElement('button');fit.type='button';fit.textContent='Все районы';
    fit.onclick=()=>map.fitBounds(layer.getBounds(),{padding:[15,15],maxZoom:11});
    box.append(toggle,fit);L.DomEvent.disableClickPropagation(box);L.DomEvent.disableScrollPropagation(box);return box;
  };
  control.addTo(map);
}
export async function renderBoundaries(map,result,markers) {
  latest=result;currentMarkers=markers;
  try {
    if(!loading)loading=fetch('/static/data/astana-districts.geojson').then(r=>{if(!r.ok)throw Error('Boundary download failed');return r.json();});
    const geo=await loading;
    if(!layer){
      layer=L.geoJSON(geo,{style,onEachFeature:(feature,polygon)=>{
        polygon.bindTooltip(feature.properties.name,{sticky:true});
        polygon.bindPopup(()=>popup(feature));
        polygon.on('mouseover',()=>{polygon.setStyle({weight:4,fillOpacity:.25});polygon.bringToFront();});
        polygon.on('mouseout',()=>layer.resetStyle(polygon));
      }}).addTo(map);
      controls(map);
      map.on('zoomend',()=>updateLabelVisibility(map));
      map.attributionControl.addAttribution('<a href="https://gis.esaulet.kz/server/rest/services/Hosted/raiony/FeatureServer/0" target="_blank" rel="noopener">Границы: геосервис Астаны</a>');
    }
    layer.eachLayer(polygon=>{polygon.setPopupContent(popup(polygon.feature));});
    // Place each scenario label inside its real polygon instead of the old approximate seed coordinate.
    markers.forEach((marker,index)=>{const id=result.changes[index]?.id;const point=geo.features.find(f=>f.properties.id===id)?.properties.label_point;if(point&&latest===result)marker.setLatLng([point[1],point[0]]);});
    updateLabelVisibility(map);
    const legend=document.querySelector('#district-boundary-legend');
    if(legend&&!legend.childElementCount){
      geo.features.forEach(feature=>{const button=document.createElement('button');button.type='button';button.className='boundary-legend-item';button.style.setProperty('--district-color',colors[feature.properties.id]);button.textContent=feature.properties.name;button.onclick=()=>{const polygon=layer.getLayers().find(l=>l.feature.properties.id===feature.properties.id);if(!show){layer.addTo(map);show=true;document.querySelector('.district-map-controls button').setAttribute('aria-pressed','true');}map.fitBounds(polygon.getBounds(),{padding:[20,20],maxZoom:12});polygon.openPopup();};legend.append(button);});
    }
  } catch(error) {
    loading=null;
    document.querySelector('#map-message').textContent='Не удалось загрузить границы районов. Обновите страницу; показатели и метки остаются доступны.';
  }
}

function updateLabelVisibility(map) {
  for(const marker of currentMarkers){const element=marker.getElement();if(element)element.style.display=map.getZoom()<10?'none':'';}
}
