// JACAPIZZA — Global JS
// Minimal global helpers; most logic is inline per template

// Achica una foto del celular (3-5 MB) a un JPEG de ~150 KB antes de subirla.
// Asi los comprobantes y facturas no llenan el volumen. Si algo falla (un PDF,
// un formato que el navegador no sabe dibujar) devuelve el archivo tal cual.
function comprimirFoto(file, lado, calidad) {
  lado = lado || 1600; calidad = calidad || 0.7;
  return new Promise(function (resolve) {
    if (!file || !/^image\/(jpeg|png|webp)$/i.test(file.type)) { resolve(file); return; }
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      var k = Math.min(1, lado / Math.max(img.width, img.height));
      var cv = document.createElement('canvas');
      cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k);
      cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
      URL.revokeObjectURL(url);
      cv.toBlob(function (blob) {
        if (!blob || blob.size >= file.size) { resolve(file); return; }
        resolve(new File([blob], 'foto.jpg', { type: 'image/jpeg' }));
      }, 'image/jpeg', calidad);
    };
    img.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
    img.src = url;
  });
}

// Pone `file` en un <input type=file> para que viaje con el formulario normal.
function ponerArchivo(input, file) {
  try { var dt = new DataTransfer(); dt.items.add(file); input.files = dt.files; return true; }
  catch (e) { return false; }
}
