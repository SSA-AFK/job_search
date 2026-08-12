import { Navigate, createBrowserRouter } from "react-router-dom";

import { SearchPage } from "../search/SearchPage";
import { CompanyDetailPage } from "../company/CompanyDetailPage";
import { DashboardPage } from "../dashboard/DashboardPage";
import { RankingListPage } from "../ranking/RankingListPage";

export const router = createBrowserRouter([
  { path: "/dashboard", element: <DashboardPage /> },
  { path: "/list", element: <RankingListPage /> },
  { path: "/companies", element: <SearchPage /> },
  { path: "/companies/:companyId", element: <CompanyDetailPage /> },
  { path: "*", element: <Navigate to="/list" replace /> },
]);
