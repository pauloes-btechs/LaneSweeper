-- Default permission matrix. Configurable per deployment by city_admin via the orchestrator (audited).
-- 'robot.command_real' is intentionally granted to NO role: real robot movement is unavailable.
insert into public.role_permissions (role, permission) values
  ('public_rider','report.create'), ('public_rider','map.view_public'),
  ('authorized_reporter','report.create'), ('authorized_reporter','map.view_public'), ('authorized_reporter','report.view_own'),

  ('city_viewer','map.view_public'), ('city_viewer','ticket.view'), ('city_viewer','fleet.view'),
  ('city_viewer','analytics.view'), ('city_viewer','knowledge.view'),

  ('city_operator','map.view_public'), ('city_operator','ticket.view'), ('city_operator','ticket.update'),
  ('city_operator','ticket.assign'), ('city_operator','evidence.view'), ('city_operator','fleet.view'),
  ('city_operator','telemetry.view'), ('city_operator','mission.propose'), ('city_operator','mission.cancel'),
  ('city_operator','fleet.stop'), ('city_operator','analytics.view'), ('city_operator','knowledge.view'),
  ('city_operator','copilot.use'), ('city_operator','notice.draft'),

  ('city_dispatcher','map.view_public'), ('city_dispatcher','ticket.view'), ('city_dispatcher','ticket.update'),
  ('city_dispatcher','ticket.assign'), ('city_dispatcher','evidence.view'), ('city_dispatcher','fleet.view'),
  ('city_dispatcher','telemetry.view'), ('city_dispatcher','mission.propose'), ('city_dispatcher','mission.approve'),
  ('city_dispatcher','mission.cancel'), ('city_dispatcher','fleet.stop'), ('city_dispatcher','analytics.view'),
  ('city_dispatcher','knowledge.view'), ('city_dispatcher','copilot.use'), ('city_dispatcher','notice.draft'),

  ('city_supervisor','map.view_public'), ('city_supervisor','ticket.view'), ('city_supervisor','ticket.update'),
  ('city_supervisor','ticket.assign'), ('city_supervisor','evidence.view'), ('city_supervisor','fleet.view'),
  ('city_supervisor','telemetry.view'), ('city_supervisor','mission.propose'), ('city_supervisor','mission.approve'),
  ('city_supervisor','mission.cancel'), ('city_supervisor','fleet.stop'), ('city_supervisor','fleet.resume'),
  ('city_supervisor','analytics.view'), ('city_supervisor','knowledge.view'), ('city_supervisor','knowledge.manage'),
  ('city_supervisor','copilot.use'), ('city_supervisor','notice.draft'), ('city_supervisor','notice.publish'),
  ('city_supervisor','verdict.override'),

  ('fleet_technician','fleet.view'), ('fleet_technician','telemetry.view'), ('fleet_technician','fleet.stop'),
  ('fleet_technician','fleet.service'), ('fleet_technician','ticket.view'),

  ('city_admin','map.view_public'), ('city_admin','ticket.view'), ('city_admin','fleet.view'),
  ('city_admin','analytics.view'), ('city_admin','knowledge.view'), ('city_admin','knowledge.manage'),
  ('city_admin','admin.membership'), ('city_admin','admin.policy'), ('city_admin','admin.budget'),

  -- platform support: operational metadata only; no protected tenant evidence by default
  ('platform_admin','map.view_public'), ('platform_admin','platform.health')
on conflict do nothing;
