"""

Title: State identification of aerosol particle size distribution
Author: Vincent Russell
Date: June 27, 2022

"""


#######################################################
# Modules:
import numpy as np
import time as tm
import matplotlib.pyplot as plt
from tkinter import mainloop
from tqdm import tqdm

# Local modules:
import basic_tools
from basic_tools import Kalman_filter, compute_fixed_interval_Kalman_smoother, compute_norm_difference
from observation_models.data.simulated import load_observations
from evolution_models.tools import GDE_evolution_model, GDE_Jacobian, compute_U, change_basis_x_to_logDp
from observation_models.tools import get_DMA_transfer_function, compute_alpha_to_z_operator, Size_distribution_observation_model


#######################################################
# Importing parameter file:
from state_identification_case_studies.case_study_12.state_iden_12_parameters import *


#######################################################
if __name__ == '__main__':

    #######################################################
    # Initialising timer for total computation:
    initial_time = tm.time()  # Initial time stamp


    #######################################################
    # Importing simulated observations and true size distribution:
    observation_data = load_observations(data_filename)  # Loading data file
    d_obs, Y = observation_data['d_obs'], observation_data['Y']  # Extracting observations
    d_true, n_x_true = observation_data['d_true'], observation_data['n_true']  # Extracting true size distribution
    n_logDp_true = change_basis_x_to_logDp(n_x_true, diameter_to_volume(d_true), d_true)  # Computing diameter-based size distribution


    #######################################################
    # Constructing size distribution evolution model:
    F_alpha = GDE_evolution_model(Ne, Np, xmin, xmax, dt, NT, boundary_zero=boundary_zero, scale_type='log')  # Initialising evolution model
    F_alpha.add_process('condensation', guess_cond)  # Adding condensation to evolution model
    F_alpha.add_unknown('deposition', Ne_eta, Np_eta)  # Adding deposition as unknown to evolution model
    # F_alpha.add_process('coagulation', coag, load_coagulation=load_coagulation, coagulation_suffix=coagulation_suffix)  # Adding coagulation to evolution model
    F_alpha.compile()  # Compiling evolution model


    #######################################################
    # Continuity constraint computations for eta (deposition):
    num_constraints_eta = Ne_eta - 1  # Number of contraints
    Nc_eta = N_eta - num_constraints_eta  # Dimensions of constrained eta
    U_eta, UT_eta = compute_U(N_eta, Ne_eta, Np_eta, F_alpha.phi_eta, F_alpha.x_boundaries_eta)  # Computing null space continuity matrix
    # Continuity constraint matrix with multiple states in time (accounting for VAR(p) model):
    U_eta_p = np.zeros([eta_p * N_eta, eta_p * Nc_eta])  # Initialising
    UT_eta_p = np.zeros([eta_p * Nc_eta, eta_p * N_eta])  # Initialising
    for i in range(eta_p):
        U_eta_p[i * N_eta: (i + 1) * N_eta, i * Nc_eta: (i + 1) * Nc_eta] = U_eta
        UT_eta_p[i * Nc_eta: (i + 1) * Nc_eta, i * N_eta: (i + 1) * N_eta] = UT_eta


    #######################################################
    # Constructing deposition rate VAR(p) evolution model:

    # A matrix (evolution operator for eta_tilde):
    A_eta = np.zeros([eta_p * N_eta, eta_p * N_eta])  # Initialising matrix
    for i in range(eta_p - 1):
        A_eta[0: N_eta, (i * N_eta): ((i + 1) * N_eta)] = eta_A[i]  # Computing elements
        A_eta[((i + 1) * N_eta): ((i + 2) * N_eta), (i * N_eta): ((i + 1) * N_eta)] = np.eye(N_eta)  # Adding identity to off-diagonal
    A_eta[0: N_eta, ((eta_p - 1) * N_eta): (eta_p * N_eta)] = eta_A[eta_p - 1]  # Computing last element

    # B matrix (modification for evolution operator for alpha):
    B_eta = np.zeros([N_eta, eta_p * N_eta])  # Initilising matrix
    for i in range(eta_p):
        B_eta[0: N_eta, (i * N_eta): ((i + 1) * N_eta)] = eta_A[i]  # Computing elements
    B_eta[0: N_eta, 0: N_eta] = B_eta[0: N_eta, 0: N_eta] + np.eye(N_eta)  # Adding one to first element

    # C matrix (extracting current state from vector of multiple states):
    C_eta = np.zeros([Nc_eta, eta_p * Nc_eta])
    C_eta[0: Nc_eta, 0: Nc_eta] = np.eye(Nc_eta)


    #######################################################
    # Constructing Jacobian of size distribution evolution model:
    J_F_alpha = GDE_Jacobian(F_alpha)  # Evolution Jacobian
    dF_alpha_d_alpha = J_F_alpha.eval_d_alpha  # Derivative with respect to alpha
    dF_alpha_d_eta = J_F_alpha.eval_d_eta  # Derivative with respect to eta


    #######################################################
    # Functions to compute/update evolution operator, Jacobians, and covariance using Crank-Nicolson method:

    # Function to compute evolution operator Jacobians:
    def compute_evolution_operator_Jacobians(x_tilde_c_star, t_star):
        # Getting current state (i.e. accounting for VAR(p) model):
        x_c_star = np.zeros(N + Nc_eta)
        x_c_star[0: N] = x_tilde_c_star[0: N]
        x_c_star[N: N + Nc_eta] = np.matmul(C_eta, x_tilde_c_star[N: N + eta_p * Nc_eta])
        x_c_star[N + Nc_eta: N + Nc_eta] = x_tilde_c_star[N + eta_p * Nc_eta: N + eta_p * Nc_eta]
        # Computing non-constrained state:
        x_star = np.zeros(N + N_eta)  # Initialising
        x_star[0: N] = x_c_star[0: N]  # Extracting alpha from constrained state x
        x_star[N: N + N_eta] = np.matmul(U_eta, x_c_star[N: N + Nc_eta])  # Extracting eta from constrained state x
        x_star[N + N_eta: N + N_eta] = x_c_star[N + Nc_eta: N + Nc_eta]  # Extracting J from constrained state x
        # Computing Jacobians:
        J_alpha_star = dF_alpha_d_alpha(x_star, t_star)
        J_eta_star = dF_alpha_d_eta(x_star, t_star)
        return J_alpha_star, J_eta_star

    # Function to compute evolution operator:
    def compute_evolution_operator(x_tilde_c_star, t_star, J_alpha_star, J_eta_star):
        # Getting current state (i.e. accounting for VAR(p) model):
        x_c_star = np.zeros(N + Nc_eta)
        x_c_star[0: N] = x_tilde_c_star[0: N]
        x_c_star[N: N + Nc_eta] = np.matmul(C_eta, x_tilde_c_star[N: N + eta_p * Nc_eta])
        # Computing non-constrained state:
        x_star = np.zeros(N + N_eta)  # Initialising
        x_star[0: N] = x_c_star[0: N]  # Extracting alpha from constrained state x
        x_star[N: N + N_eta] = np.matmul(U_eta, x_c_star[N: N + Nc_eta])  # Extracting eta from constrained state x
        # Extracting coefficients from state:
        alpha_star = x_star[0: N]
        eta_star = x_star[N: N + N_eta]
        # Computing F_star:
        F_star = F_alpha.eval(x_star, t_star) - np.matmul(J_alpha_star, alpha_star) - np.matmul(J_eta_star, eta_star)
        # Computing evolution operators for each coefficient:
        matrix_multiplier = np.linalg.inv(np.eye(N) - (dt / 2) * J_alpha_star)  # Computing matrix multiplier for evolution operators and additive vector
        F_evol_alpha = np.matmul(matrix_multiplier, (np.eye(N) + (dt / 2) * J_alpha_star))
        F_evol_eta = np.matmul(matrix_multiplier, ((dt / 2) * J_eta_star))
        # Updating evolution models to account for autoregressive models:
        F_evol_eta = np.matmul(F_evol_eta, B_eta)
        # Updating evolution models to account for continuity constraint:
        F_evol_eta = np.matmul(F_evol_eta, U_eta_p)
        # Computing evolution operator:
        F_evolution = np.zeros([N + eta_p * Nc_eta, N + eta_p * Nc_eta])  # Initialising
        F_evolution[0:N, 0:N] = F_evol_alpha
        F_evolution[0:N, N: N + eta_p * Nc_eta] = F_evol_eta
        # Row for eta:
        F_evolution[N: N + eta_p * Nc_eta, N: N + eta_p * Nc_eta] = np.matmul(UT_eta_p, np.matmul(A_eta, U_eta_p))
        # Computing evolution additive vector:
        b_evolution = np.zeros(N + eta_p * Nc_eta)  # Initialising
        b_evolution[0:N] = np.matmul(matrix_multiplier, (dt * F_star))
        return F_evolution, b_evolution

    # Function to compute evolution operator covariance:
    def compute_evolution_operator_alpha_covariance(Gamma_tilde_c_w, J_alpha_star, J_eta_star):
        # Getting current state covariance (i.e. accounting for VAR(p) model):
        Gamma_c_w = np.zeros([N + Nc_eta, N + Nc_eta])
        Gamma_c_w[0:N, 0:N] = Gamma_tilde_c_w[0:N, 0:N]
        Gamma_c_w[N: N + Nc_eta, N: N + Nc_eta] = Gamma_tilde_c_w[N: N + Nc_eta, N: N + Nc_eta]
        # Computing non-constrained state covariances:
        Gamma_alpha_w = Gamma_c_w[0:N, 0:N]
        Gamma_eta_w = np.matmul(U_eta, np.matmul(Gamma_c_w[N: N + Nc_eta, N: N + Nc_eta], UT_eta))
        # Precomputations:
        matrix_multiplier = np.linalg.inv(np.eye(N) - (dt / 2) * J_alpha_star)
        Gamma_Jac_eta = np.matmul(J_eta_star, np.matmul(Gamma_eta_w, np.transpose(J_eta_star)))
        # Computing output:
        return Gamma_alpha_w + (dt ** 2 / 4) * np.matmul(matrix_multiplier, np.matmul(Gamma_Jac_eta, np.transpose(matrix_multiplier)))


    #######################################################
    # Constructing observation model:
    if use_DMPS_observation_model:
        M = N_channels  # Setting M to number of channels (i.e. observation dimensions to number of channels)
        DMA_transfer_function = get_DMA_transfer_function(R_inner, R_outer, length, Q_aerosol, Q_sheath, efficiency)  # Computes DMA transfer function
        H_alpha = compute_alpha_to_z_operator(F_alpha, DMA_transfer_function, N_channels, voltage_min, voltage_max)  # Computes operator for computing z(t) given alpha(t)
        H = np.zeros([M, N + eta_p * Nc_eta])  # Initialising
        H[0:M, 0:N] = H_alpha  # Observation operator
    else:
        M = len(Y)  # Dimension size of observations
        H_alpha = Size_distribution_observation_model(F_alpha, d_obs, M)  # Observation model
        H = np.zeros([M, N + eta_p * Nc_eta])  # Initialising
        H[0:M, 0:N] = H_alpha.H_phi  # Observation operator


    #######################################################
    # Constructing noise covariance for observation model:
    Gamma_v = np.zeros([NT, M, M])  # Initialising
    for k in range(NT):
        Gamma_Y_multiplier = (sigma_Y_multiplier ** 2) * np.diag(Y[:, k])  # Noise proportional to Y
        Gamma_v_additive = (sigma_v ** 2) * np.eye(M)  # Additive noise
        Gamma_v[k] = Gamma_Y_multiplier + Gamma_v_additive  # Observation noise covariance


    #######################################################
    # Constructing noise covariances for evolution model:

    # For alpha:
    sigma_alpha_w = np.array([sigma_alpha_w_0, sigma_alpha_w_1, sigma_alpha_w_2, sigma_alpha_w_3, sigma_alpha_w_4, sigma_alpha_w_5, sigma_alpha_w_6])  # Array of standard deviations
    Gamma_alpha_w = basic_tools.compute_correlated_covariance_matrix(N, Np, Ne, sigma_alpha_w, sigma_alpha_w_correlation, use_element_multiplier=alpha_use_element_multipler)  # Covariance matrix computation

    # For eta:
    sigma_eta_w = np.array([sigma_eta_w_0, sigma_eta_w_1, sigma_eta_w_2, sigma_eta_w_3, sigma_eta_w_4, sigma_eta_w_5, sigma_eta_w_6])  # Array of standard deviations
    Gamma_eta_w = basic_tools.compute_correlated_covariance_matrix(N_eta, Np_eta, Ne_eta, sigma_eta_w, sigma_eta_w_correlation, use_element_multiplier=eta_use_element_multipler)  # Covariance matrix computation
    Gamma_eta_tilde_w = np.zeros([eta_p * N_eta, eta_p * N_eta])  # Initialising covariance for eta_tilde
    Gamma_eta_tilde_w[0: N_eta, 0: N_eta] = Gamma_eta_w  # Covariance for eta_tilde
    Gamma_eta_tilde_c_w = np.matmul(UT_eta_p, np.matmul(Gamma_eta_tilde_w, U_eta_p))  # Continuity constraint conversion

    # Assimilation:
    Gamma_tilde_c_w = np.zeros([N + eta_p * Nc_eta, N + eta_p * Nc_eta])  # Initialising noise covariance for state
    Gamma_tilde_c_w[0:N, 0:N] = Gamma_alpha_w  # Adding alpha covariance to state covariance
    Gamma_tilde_c_w[N: N + eta_p * Nc_eta, N: N + eta_p * Nc_eta] = Gamma_eta_tilde_c_w  # Adding eta covariance to state covariance


    #######################################################
    # Constructing prior and prior covariance:

    # For alpha:
    alpha_prior = F_alpha.compute_coefficients('alpha', initial_guess_size_distribution)  # Computing alpha coefficients from initial condition function
    sigma_alpha_prior = np.array([sigma_alpha_prior_0, sigma_alpha_prior_1, sigma_alpha_prior_2, sigma_alpha_prior_3, sigma_alpha_prior_4, sigma_alpha_prior_5, sigma_alpha_prior_6])  # Array of standard deviations
    Gamma_alpha_prior = basic_tools.compute_correlated_covariance_matrix(N, Np, Ne, sigma_alpha_prior, 0.001, use_element_multiplier=alpha_use_element_multipler)  # Covariance matrix computation

    # For eta:
    eta_prior = F_alpha.compute_coefficients('eta', initial_guess_deposition_rate)    # Computing eta coefficients from initial guess of deposition rate
    sigma_eta_prior = np.array([sigma_eta_prior_0, sigma_eta_prior_1, sigma_eta_prior_2, sigma_eta_prior_3, sigma_eta_prior_4, sigma_eta_prior_5, sigma_eta_prior_6])  # Array of standard deviations
    Gamma_eta_prior = basic_tools.compute_correlated_covariance_matrix(N_eta, Np_eta, Ne_eta, sigma_eta_prior, 0.001, use_element_multiplier=eta_use_element_multipler)  # Covariance matrix computation
    # Prior for eta_tilde:
    eta_tilde_prior = np.zeros([eta_p * N_eta])
    Gamma_eta_tilde_prior = np.zeros([eta_p * N_eta, eta_p * N_eta])
    for i in range(eta_p):
        eta_tilde_prior[i * N_eta: (i + 1) * N_eta] = eta_prior
        Gamma_eta_tilde_prior[i * N_eta: (i + 1) * N_eta, i * N_eta: (i + 1) * N_eta] = Gamma_eta_prior
    # Continuity constraint conversion:
    eta_tilde_c_prior = np.matmul(UT_eta_p, eta_tilde_prior)
    Gamma_eta_tilde_c_prior = np.matmul(UT_eta_p, np.matmul(Gamma_eta_tilde_prior, U_eta_p))

    # Assimilation:
    x_tilde_c_prior = np.zeros([N + eta_p * Nc_eta])  # Initialising prior state
    x_tilde_c_prior[0:N] = alpha_prior  # Adding alpha prior to prior state
    x_tilde_c_prior[N: N + eta_p * Nc_eta] = eta_tilde_c_prior  # Adding eta prior to prior state
    Gamma_tilde_c_prior = np.zeros([N + eta_p * Nc_eta, N + eta_p * Nc_eta])  # Initialising prior covariance for state
    Gamma_tilde_c_prior[0:N, 0:N] = Gamma_alpha_prior  # Adding alpha covariance to state covariance
    Gamma_tilde_c_prior[N: N + eta_p * Nc_eta, N: N + eta_p * Nc_eta] = Gamma_eta_tilde_c_prior  # Adding eta covariance to state covariance


    #######################################################
    # Initialising state and adding prior:
    x_tilde_c = np.zeros([N + eta_p * Nc_eta, NT])  # Initialising state = [x_0, x_1, ..., x_{NT - 1}]
    Gamma_tilde_c = np.zeros([NT, N + eta_p * Nc_eta, N + eta_p * Nc_eta])  # Initialising state covariance matrix Gamma_0, Gamma_1, ..., Gamma_NT
    x_tilde_c_predict = np.zeros([N + eta_p * Nc_eta, NT])  # Initialising predicted state
    Gamma_tilde_c_predict = np.zeros([NT, N + eta_p * Nc_eta, N + eta_p * Nc_eta])  # Initialising predicted state covariance
    x_tilde_c[:, 0], x_tilde_c_predict[:, 0] = x_tilde_c_prior, x_tilde_c_prior  # Adding prior to states
    Gamma_tilde_c[0], Gamma_tilde_c_predict[0] = Gamma_tilde_c_prior, Gamma_tilde_c_prior  # Adding prior to state covariance matrices


    #######################################################
    # Initialising evolution operator and additive evolution vector:
    F = np.zeros([NT, N + eta_p * Nc_eta, N + eta_p * Nc_eta])  # Initialising evolution operator F_0, F_1, ..., F_{NT - 1}
    b = np.zeros([N + eta_p * Nc_eta, NT])  # Initialising additive evolution vector b_0, b_1, ..., b_{NT - 1}


    #######################################################
    # Constructing Kalman filter model:
    if use_BAE:
        BAE_data = np.load(filename_BAE + '.npz')  # Loading BAE data
        BAE_mean, BAE_covariance = BAE_data['BAE_mean'], BAE_data['BAE_covariance']  # Extracting mean and covariance from data
        model = Kalman_filter(F[0], H, Gamma_tilde_c_w, Gamma_v, NT, additive_evolution_vector=b[:, 0], mean_epsilon=BAE_mean, Gamma_epsilon=BAE_covariance)
    else:
        model = Kalman_filter(F[0], H, Gamma_tilde_c_w, Gamma_v, NT, additive_evolution_vector=b[:, 0])


    #######################################################
    # Computing time evolution of model:
    print('Computing Kalman filter estimates...')
    t = np.zeros(NT)  # Initialising time array
    for k in tqdm(range(NT - 1)):  # Iterating over time
        J_alpha_star, J_eta_star = compute_evolution_operator_Jacobians(x_tilde_c[:, k], t[k])  # Computing evolution operator Jacobian
        F[k], b[:, k] = compute_evolution_operator(x_tilde_c[:, k], t[k], J_alpha_star, J_eta_star)  # Computing evolution operator F and vector b
        model.F, model.additive_evolution_vector = F[k], b[:, k]  # Adding updated evolution operator and vector b to Kalman Filter
        model.Gamma_w[0:N, 0:N] = compute_evolution_operator_alpha_covariance(model.Gamma_w, J_alpha_star, J_eta_star)  # Computing evolution model covariance matrix for alpha coefficients
        x_tilde_c_predict[:, k + 1], Gamma_tilde_c_predict[k + 1] = model.predict(x_tilde_c[:, k], Gamma_tilde_c[k], k)  # Computing prediction
        x_tilde_c[:, k + 1], Gamma_tilde_c[k + 1] = model.update(x_tilde_c_predict[:, k + 1], Gamma_tilde_c_predict[k + 1], Y[:, k + 1], k)  # Computing update
        t[k + 1] = (k + 1) * dt  # Time (hours)


    #######################################################
    # Computing smoothed estimates:
    if smoothing:
        print('Computing Kalman smoother estimates...')
        x_tilde_c, Gamma_tilde_c = compute_fixed_interval_Kalman_smoother(F, NT, N + eta_p * Nc_eta, x_tilde_c, Gamma_tilde_c, x_tilde_c_predict, Gamma_tilde_c_predict)


    #######################################################
    # Computing single state from multiple state vector (computing x_c from x_tilde_c):
    x_c = np.zeros([N + Nc_eta, NT])
    Gamma_c = np.zeros([NT, N + Nc_eta, N + Nc_eta])
    for k in range(NT):
        x_c[0: N, k] = x_tilde_c[0: N, k]
        x_c[N: N + Nc_eta, k] = x_tilde_c[N: N + Nc_eta, k]
        Gamma_c[k, 0: N, 0: N] = Gamma_tilde_c[k, 0: N, 0: N]
        Gamma_c[k, N: N + Nc_eta, N: N + Nc_eta] = Gamma_tilde_c[k, N: N + Nc_eta, N: N + Nc_eta]


    #######################################################
    # Computing unconstrained state:
    x = np.zeros([N + N_eta, NT])  # Initialising state = [x_0, x_1, ..., x_{NT - 1}]
    Gamma = np.zeros([NT, N + N_eta, N + N_eta])  # Initialising state covariance matrix Gamma_0, Gamma_1, ..., Gamma_NT
    for k in range(NT):
        x[0: N, k] = x_c[0: N, k]  # Extracting alpha from constrained state x
        x[N: N + N_eta, k] = np.matmul(U_eta, x_c[N: N + Nc_eta, k])  # Extracting eta from constrained state x
        Gamma[k, 0: N, 0: N] = Gamma_c[k, 0: N, 0: N]
        Gamma[k, N: N + N_eta, N: N + N_eta] = np.matmul(U_eta, np.matmul(Gamma_c[k, N: N + Nc_eta, N: N + Nc_eta], UT_eta))


    #######################################################
    # Extracting alpha, gamma, eta, and covariances from state:
    alpha = x[0:N, :]  # Size distribution coefficients
    eta = x[N: N + N_eta, :]  # Deposition rate coefficients
    Gamma_alpha = Gamma[:, 0:N, 0:N]  # Size distribution covariance
    Gamma_eta = Gamma[:, N: N + N_eta, N: N + N_eta]  # Deposition rate covariance


    #######################################################
    # Computing plotting discretisation:
    # Size distribution:
    d_plot, v_plot, n_logDp_plot, sigma_n_logDp = F_alpha.get_nplot_discretisation(alpha, Gamma_alpha=Gamma_alpha, convert_x_to_logDp=True)
    n_logDp_plot_upper = n_logDp_plot + 2 * sigma_n_logDp
    n_logDp_plot_lower = n_logDp_plot - 2 * sigma_n_logDp
    # Deposition rate:
    _, log_d_plot_depo, depo_plot, sigma_depo = F_alpha.get_parameter_estimation_discretisation('deposition', eta, Gamma_eta)
    depo_plot_upper = depo_plot + 2 * sigma_depo
    depo_plot_lower = depo_plot - 2 * sigma_depo


    #######################################################
    # Computing true underlying parameters plotting discretisation:
    Nplot_cond = len(d_plot)  # Length of size discretisation
    Nplot_depo = len(log_d_plot_depo)  # Length of size discretisation
    cond_Dp_truth_plot = np.zeros([Nplot_cond, NT])  # Initialising condensation rate
    cond_Dp_guess_plot = np.zeros([Nplot_cond, NT])  # Initialising condensation rate
    d_plot_depo = np.exp(log_d_plot_depo)  # Computing Dp plotting discretisation
    depo_true_plot = np.zeros([Nplot_depo, NT])  # Initialising deposition rate
    for k in range(NT):
        for i in range(Nplot_cond):
            cond_Dp_truth_plot[i, k] = cond(d_plot[i])  # Computing condensation rate
            cond_Dp_guess_plot[i, k] = guess_cond(d_plot[i])  # Computing condensation rate
        for i in range(Nplot_depo):
            depo_true_plot[i, k] = depo(d_plot_depo[i])  # Computing deposition rate


    #######################################################
    # Computing norm difference between truth and estimates:

    # Size distribution:
    x_true = np.log(basic_tools.diameter_to_volume(d_true))
    _, _, n_x_estimate, sigma_n_x = F_alpha.get_nplot_discretisation(alpha, Gamma_alpha=Gamma_alpha, x_plot=x_true)  # Computing estimate on true discretisation
    norm_diff = compute_norm_difference(n_x_true, n_x_estimate, sigma_n_x, compute_weighted_norm=compute_weighted_norm, print_name='size distribution')  # Computing norm difference

    # Deposition rate:
    norm_diff_depo = compute_norm_difference(depo_true_plot, depo_plot, sigma_depo, compute_weighted_norm=compute_weighted_norm, print_name='deposition rate')  # Computing norm difference


    #######################################################
    # Printing total computation time:
    computation_time = round(tm.time() - initial_time, 3)  # Initial time stamp
    print('Total computation time:', str(computation_time), 'seconds.')  # Print statement


    #######################################################
    # Plotting size distribution animation and function parameters:

    # General parameters:
    location = 'Home'  # Set to 'Uni', 'Home', or 'Middle' (default)

    # Parameters for size distribution animation:
    xscale = 'log'  # x-axis scaling ('linear' or 'log')
    xticks = [0.1, 1, 10]  # Plot x-tick labels
    xlimits = [0.1, 10]  # Plot boundary limits for x-axis
    ylimits = [0, 10000]  # Plot boundary limits for y-axis
    xlabel = '$D_p$ ($\mu$m)'  # x-axis label for 1D animation plot
    ylabel = '$\dfrac{dN}{dlogD_p}$ (cm$^{-3})$'  # y-axis label for 1D animation plot
    title = 'Size distribution estimation'  # Title for 1D animation plot
    legend = ['Estimate', '$\pm 2 \sigma$', '', 'Truth']  # Adding legend to plot
    line_color = ['blue', 'blue', 'blue', 'green']  # Colors of lines in plot
    line_style = ['solid', 'dashed', 'dashed', 'solid']  # Style of lines in plot
    time = t  # Array where time[i] is plotted (and animated)
    timetext = ('Time = ', ' hours')  # Tuple where text to be animated is: timetext[0] + 'time[i]' + timetext[1]
    delay = 60  # Delay between frames in milliseconds

    # Parameters for condensation plot:
    yscale_cond = 'linear'  # y-axis scaling ('linear' or 'log')
    ylimits_cond = [1e-3, 1e1]  # Plot boundary limits for y-axis
    xlabel_cond = '$D_p$ ($\mu$m)'  # x-axis label for plot
    ylabel_cond = '$I(D_p)$ ($\mu$m hour$^{-1}$)'  # y-axis label for plot
    title_cond = 'Condensation rate estimation'  # Title for plot
    location_cond = location + '2'  # Location for plot
    legend_cond = ['Guess', 'Truth']  # Adding legend to plot
    line_color_cond = ['blue', 'green']  # Colors of lines in plot
    line_style_cond = ['solid', 'solid']  # Style of lines in plot
    delay_cond = 60  # Delay for each frame (ms)

    # Parameters for deposition plot:
    yscale_depo = 'linear'  # y-axis scaling ('linear' or 'log')
    ylimits_depo = [1e-2, 1e1]  # Plot boundary limits for y-axis
    xlabel_depo = '$D_p$ ($\mu$m)'  # x-axis label for plot
    ylabel_depo = '$d(D_p)$ (hour$^{-1}$)'  # y-axis label for plot
    title_depo = 'Deposition rate estimation'  # Title for plot
    location_depo = location + '3'  # Location for plot
    legend_depo = ['Estimate', '$\pm 2 \sigma$', '', 'Truth']  # Adding legend to plot
    line_color_depo = ['blue', 'blue', 'blue', 'green']  # Colors of lines in plot
    line_style_depo = ['solid', 'dashed', 'dashed', 'solid']  # Style of lines in plot
    delay_depo = delay_cond  # Delay between frames in milliseconds

    # Size distribution animation:
    basic_tools.plot_1D_animation(d_plot, n_logDp_plot, n_logDp_plot_lower, n_logDp_plot_upper, plot_add=(d_true, n_logDp_true), xticks=xticks, xlimits=xlimits, ylimits=ylimits, xscale=xscale, xlabel=xlabel, ylabel=ylabel, title=title,
                                  delay=delay, location=location, legend=legend, time=time, timetext=timetext, line_color=line_color, line_style=line_style, doing_mainloop=False)

    # Condensation rate animation:
    basic_tools.plot_1D_animation(d_plot, cond_Dp_guess_plot, cond_Dp_truth_plot, xticks=xticks, xlimits=xlimits, xscale=xscale, xlabel=xlabel_cond, ylabel=ylabel_cond, title=title_cond,
                                  delay=delay_cond, ylimits=ylimits_cond, yscale=yscale_cond, location=location_cond, legend=legend_cond, time=time, timetext=timetext, line_color=line_color_cond, line_style=line_style_cond, doing_mainloop=False)

    # Deposition rate animation:
    basic_tools.plot_1D_animation(d_plot_depo, depo_plot, depo_plot_lower, depo_plot_upper, depo_true_plot, xticks=xticks, xlimits=xlimits, xscale=xscale, xlabel=xlabel_depo, ylabel=ylabel_depo, title=title_depo,
                                  delay=delay_depo, ylimits=ylimits_depo, yscale=yscale_depo, location=location_depo, legend=legend_depo, time=time, timetext=timetext, line_color=line_color_depo, line_style=line_style_depo, doing_mainloop=False)

    # Mainloop and print:
    if plot_animations:
        print('Plotting animations...')
        mainloop()  # Runs tkinter GUI for plots and animations


    #######################################################
    # Plotting norm difference between truth and estimates:
    if plot_norm_difference:
        print('Plotting norm difference between truth and estimates...')

        # Size distribution:
        plt.figure()
        plt.plot(t, norm_diff)
        plt.xlim([0, T])
        plt.ylim([np.min(norm_diff), np.max(norm_diff)])
        plt.xlabel('$t$', fontsize=15)
        plt.ylabel(r'||$n_{est}(x, t) - n_{true}(x, t)$||$_W$', fontsize=14)
        plt.grid()
        plot_title = 'norm difference of size distribution estimate and truth'
        if compute_weighted_norm:
            plot_title = 'Weighted ' + plot_title
        if use_BAE:
            plot_title = plot_title + ' using BAE'
        plt.title(plot_title, fontsize=11)

        # Deposition rate:
        plt.figure()
        plt.plot(t, norm_diff_depo)
        plt.xlim([0, T])
        plt.ylim([np.min(norm_diff_depo), np.max(norm_diff_depo)])
        plt.xlabel('$t$', fontsize=15)
        plt.ylabel(r'||$d_{est}(x, t) - d_{true}(x, t)$||$_W$', fontsize=14)
        plt.grid()
        plot_title = 'norm difference of deposition rate estimate and truth'
        if compute_weighted_norm:
            plot_title = 'Weighted ' + plot_title
        if use_BAE:
            plot_title = plot_title + ' using BAE'
        plt.title(plot_title, fontsize=11)


    #######################################################
    # Images:

    # Parameters for size distribution images:
    yscale_image = 'log'  # Change scale of y-axis (linear or log)
    yticks_image = xticks  # Plot y-tick labels
    xlabel_image = 'Time (hours)'  # x-axis label for image
    ylabel_image = '$D_p$ ($\mu$m) \n'  # y-axis label for image
    ylabelcoords = (-0.06, 0.96)  # y-axis label coordinates
    title_image = 'Size distribution estimation'  # Title for image
    title_image_observations = 'Simulated observations'  # Title for image
    image_min = 10  # Minimum of image colour
    image_max = 10000  # Maximum of image colour
    cmap = 'jet'  # Colour map of image
    cbarlabel = '$\dfrac{dN}{dlogD_p}$ (cm$^{-3})$'  # Label of colour bar
    cbarticks = [10, 100, 1000, 10000]  # Ticks of colorbar

    # Plotting images:
    if plot_images:
        print('Plotting images...')
        basic_tools.image_plot(time, d_plot, n_logDp_plot, ylimits=xlimits, xlabel=xlabel_image, ylabel=ylabel_image, title=title_image,
                               yscale=yscale_image, ylabelcoords=ylabelcoords, yticks=yticks_image, image_min=image_min, image_max=image_max, cmap=cmap, cbarlabel=cbarlabel, cbarticks=cbarticks)
        basic_tools.image_plot(time, d_obs, Y, ylimits=xlimits, xlabel=xlabel_image, ylabel=ylabel_image, title=title_image_observations,
                               yscale=yscale_image, ylabelcoords=ylabelcoords, yticks=yticks_image, image_min=image_min, image_max=image_max, cmap=cmap, cbarlabel=cbarlabel, cbarticks=cbarticks)


    # Final print statements
    basic_tools.print_lines()  # Print lines in console
    print()  # Print space in console
