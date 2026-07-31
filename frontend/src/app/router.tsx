import { Navigate, createBrowserRouter } from "react-router-dom";

import { SearchPage } from "../search/SearchPage";
import { CompanyDetailPage } from "../company/CompanyDetailPage";

export const router = createBrowserRouter([
  { path: "/companies", element: <SearchPage /> },
  { path: "/companies/:companyId", element: <CompanyDetailPage /> },
  { path: "*", element: <Navigate to="/companies" replace /> },
]);
