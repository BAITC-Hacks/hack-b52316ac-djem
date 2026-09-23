// Surface proxy/login HTML as an actionable error rather than a JSON syntax exception.
export async function parseApiResponse(response,path){
 const raw=await response.text();let value;
 try{value=JSON.parse(raw);}catch{
  const status=response.status;
  const reason=response.redirected?'Запрос перенаправлен на другую страницу. Проверьте вход в Brev.':status===502||status===503?'Python-сервис временно недоступен. Проверьте запуск приложения на порту 8080.':status===504?'Сервер или прокси не дождался ответа. Повторите запрос; готовый AI-разбор может быть уже сохранён.':status===404?'Маршрут API не найден. Проверьте, что сервер обновлён вместе с интерфейсом.':'Сервер вернул страницу HTML или некорректный ответ вместо данных API.';
  throw new Error(`Ошибка API /api/${path.split('?')[0]} (HTTP ${status}). ${reason}`);
 }
 if(!response.ok){const detail=value?.detail;const error=new Error(typeof detail==='string'?detail:detail?JSON.stringify(detail):`Ошибка API: HTTP ${response.status}`);error.status=response.status;throw error;}
 return value;
}
