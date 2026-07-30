module "network" {
  source = "./modules/network"

  name               = "${var.project_name}-${var.environment}"
  vpc_cidr           = var.vpc_cidr
  public_subnet_cidr = var.public_subnet_cidr
}

module "observability" {
  source = "./modules/observability"

  instance_name  = var.instance_name
  retention_days = var.log_retention_days
}

module "compute" {
  source = "./modules/compute"

  name                  = "${var.project_name}-${var.environment}"
  instance_name         = var.instance_name
  instance_type         = var.instance_type
  root_volume_size      = var.root_volume_size
  subnet_id             = module.network.public_subnet_id
  security_group_id     = module.network.security_group_id
  system_log_group_name = module.observability.system_log_group_name
  nginx_log_group_name  = module.observability.nginx_log_group_name
}

module "mcp_readonly" {
  source = "./modules/mcp_readonly"

  name                         = "${var.project_name}-${var.environment}-diagnostics-readonly"
  runtime_role_name            = "aws-infra-ops-mcp-lab-runtime"
  trusted_sso_role_arn_pattern = var.trusted_sso_role_arn_pattern
  instance_arn                 = module.compute.instance_arn
  log_group_arns = [
    module.observability.system_log_group_arn,
    module.observability.nginx_log_group_arn,
  ]
}
