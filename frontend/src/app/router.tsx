import { Navigate, createBrowserRouter } from "react-router-dom";

import { SearchPage } from "../search/SearchPage";

export const router = createBrowserRouter([
  { path: "/companies", element: <SearchPage /> },
  { path: "*", element: <Navigate to="/companies" replace /> },
]);
