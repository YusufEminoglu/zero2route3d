// Globals the viewer puts on window (app3d.js) for the live link and the
// offline export.
interface Window {
  app3d?: { loadRoute(data: unknown): void };
  setRouteData?: (data: unknown) => void;
}
