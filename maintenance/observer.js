// 只读取可见 UI 并截图。不能修改响应、广告方法、证书或点击跳过。
'use strict';
function mainThread(fn) {
  return new Promise((resolve, reject) => {
    ObjC.schedule(ObjC.mainQueue, () => {
      const pool = ObjC.classes.NSAutoreleasePool.alloc().init();
      try { resolve(fn()); } catch (e) { reject(String(e.stack || e)); }
      finally { pool.release(); }
    });
  });
}
function strings(view, result, depth) {
  if (depth > 16 || result.length > 250 || view.isHidden() || Number(view.alpha()) < .01) return;
  for (const selector of ['text', 'title', 'accessibilityLabel']) {
    try {
      if (view.respondsToSelector_(ObjC.selector(selector))) {
        const v = view[selector]();
        if (v && v.toString() && v.toString() !== 'nil') result.push(v.toString());
      }
    } catch (_) {}
  }
  const children = view.subviews();
  for (let i = 0; i < children.count(); i++) strings(children.objectAtIndex_(i), result, depth + 1);
}
rpc.exports = {
  snapshot() {
    return mainThread(() => {
      if (!ObjC.available) throw new Error('Objective-C unavailable');
      const app = ObjC.classes.UIApplication.sharedApplication();
      const windows = app.windows();
      let window = null;
      for (let i = 0; i < windows.count(); i++) {
        const candidate = windows.objectAtIndex_(i);
        if (!candidate.isHidden() && Number(candidate.alpha()) > .01 && Number(candidate.windowLevel()) === 0) {
          window = candidate;
          if (candidate.isKeyWindow()) break;
        }
      }
      if (!window) throw new Error('No visible application window');
      const rect = window.bounds();
      const begin = new NativeFunction(Module.getGlobalExportByName('UIGraphicsBeginImageContextWithOptions'), 'void', [['double', 'double'], 'bool', 'double']);
      const get = new NativeFunction(Module.getGlobalExportByName('UIGraphicsGetImageFromCurrentImageContext'), 'pointer', []);
      const end = new NativeFunction(Module.getGlobalExportByName('UIGraphicsEndImageContext'), 'void', []);
      const jpeg = new NativeFunction(Module.getGlobalExportByName('UIImageJPEGRepresentation'), 'pointer', ['pointer', 'double']);
      begin(rect[1], false, .75);
      try {
        if (!window.drawViewHierarchyInRect_afterScreenUpdates_(rect, false)) throw new Error('Window capture returned false');
        const image = new ObjC.Object(get());
        const data = new ObjC.Object(jpeg(image.handle, .7));
        const text = []; strings(window, text, 0);
        return { jpeg: data.base64EncodedStringWithOptions_(0).toString(), texts: Array.from(new Set(text)),
                 width: rect[1][0], height: rect[1][1], applicationState: Number(app.applicationState()) };
      } finally { end(); }
    });
  },
  lockstate() {
    return mainThread(() => {
      const C = ObjC.classes.SBLockScreenManager;
      if (!C) throw new Error('Lock screen manager unavailable');
      const manager = C.sharedInstance();
      if (!manager.respondsToSelector_(ObjC.selector('isUILocked'))) throw new Error('Lock query unavailable');
      return { locked: Boolean(manager.isUILocked()) };
    });
  }
};
