variable "name" {
  type        = string
  description = "Stack name, used as the prefix for every resource."
}

variable "region" {
  type        = string
  description = "The project's selected AWS Region."
  default     = "us-east-1"
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type for the web server."
  default     = "t4g.nano"
}

variable "ttl_minutes" {
  type        = number
  description = "Minutes before the pipeline destroys the stack (0 = keep)."
  default     = 30
}
